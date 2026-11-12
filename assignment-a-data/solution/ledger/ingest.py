"""Ingestion: lead CSVs and event JSONL files, one transaction per file.

Idempotency: every file is recorded in ``ingest_files`` keyed by
(drop name, relative path) together with its SHA-256. Re-running a drop skips
files already recorded. The record and the file's data are written in the same
transaction, so a crash never leaves "recorded but half-loaded".
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import normalize as nz
from .pii import PiiVault
from .schema import Field, Schema, load_dispositions

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
EVENT_BATCH = 20_000


class IngestError(Exception):
    pass


# ======================================================================= leads
def parse_value(f: Field, raw: str) -> Optional[str]:
    """Parse one trimmed, non-empty cell by field type. None = invalid."""
    if f.type == "phone":
        return nz.normalize_phone(raw)
    if f.type == "date":
        return nz.parse_date(raw)
    if f.type == "decimal":
        return nz.parse_decimal(raw)
    if f.type == "enum":
        low = raw.lower()
        if not f.values:
            return low
        return low if low in {v.lower() for v in f.values} else None
    return raw  # string


def evaluate_row(schema: Schema, raws: Dict[str, Optional[str]]
                 ) -> Tuple[Optional[str], Dict[str, Optional[str]], int]:
    """Walk fields in schema order. -> (reject_reason | None, values, warnings).

    Calls we made where the spec is silent (see SUBMISSION.md):
    * a *required* non-phone field that is present but invalid rejects the row
      as ``missing_required:<field>`` (an unusable value is a missing value);
    * an optional phone-type field that is invalid is a warning, not a rejection;
      only the identity phone (first phone field) rejects with ``invalid_phone``.
    """
    identity = schema.phone_field.name
    values: Dict[str, Optional[str]] = {}
    warnings = 0
    for f in schema.fields:
        raw = raws.get(f.name)
        if raw is None:
            if f.required:
                return f"missing_required:{f.name}", values, 0
            values[f.name] = None
            continue
        parsed = parse_value(f, raw)
        if parsed is None:
            if f.type == "phone" and (f.required or f.name == identity):
                return "invalid_phone", values, 0
            if f.required:
                return f"missing_required:{f.name}", values, 0
            warnings += 1
            values[f.name] = None
            continue
        values[f.name] = parsed
    return None, values, warnings


@dataclass
class LeadStats:
    rows_total: int = 0
    rejected: int = 0
    merged: int = 0
    warnings: int = 0


def _store(vault: PiiVault, f: Field, value: str) -> Tuple[str, int]:
    # Phone-type values are always encrypted, even if the schema forgot to mark them pii.
    return (vault.encrypt(value), 1) if (f.pii or f.type == "phone") else (value, 0)


def ingest_lead_file(conn: sqlite3.Connection, vault: PiiVault, schema: Schema,
                     path: Path, file_id: int, batch_id: str,
                     identities: Dict[str, int]) -> LeadStats:
    """``identities``: phone_hash -> lead_id for this workflow (kept in sync)."""
    stats = LeadStats()
    by_name = {f.name: f for f in schema.fields}
    lookup = schema.header_lookup()
    phone_name = schema.phone_field.name
    upserts: List[Tuple[int, str, str, int]] = []
    touched: Set[int] = set()
    rejections: List[Tuple[int, str, str, int, str]] = []

    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return stats
        colmap: Dict[str, int] = {}
        for idx, h in enumerate(header):
            fld = lookup.get(nz.normalize_header(h))
            if fld is not None and fld.name not in colmap:  # first matching column wins
                colmap[fld.name] = idx

        row_number = 0
        for row in reader:
            if all(not c.strip() for c in row):
                continue  # blank rows are skipped and not counted
            row_number += 1
            stats.rows_total += 1
            raws: Dict[str, Optional[str]] = {}
            for name, idx in colmap.items():
                cell = row[idx].strip() if idx < len(row) else ""
                raws[name] = cell or None

            reason, values, warns = evaluate_row(schema, raws)
            if reason:
                stats.rejected += 1
                rejections.append((file_id, schema.workflow_id, batch_id, row_number, reason))
                continue
            stats.warnings += warns

            canonical = values[phone_name]
            phash = vault.phone_hash(canonical)
            lead_id = identities.get(phash)
            if lead_id is None:
                cur = conn.execute(
                    "INSERT INTO leads (workflow_id, phone_hash, phone_last4, batch_id, source_file_id)"
                    " VALUES (?,?,?,?,?)",
                    (schema.workflow_id, phash, canonical[-4:], batch_id, file_id))
                lead_id = cur.lastrowid
                identities[phash] = lead_id
            else:
                stats.merged += 1
            touched.add(lead_id)
            for name, val in values.items():
                if val is not None:  # last write wins, only where non-null
                    enc, flag = _store(vault, by_name[name], val)
                    upserts.append((lead_id, name, enc, flag))

    conn.executemany(
        "INSERT INTO lead_fields (lead_id, field_name, value, is_encrypted) VALUES (?,?,?,?)"
        " ON CONFLICT (lead_id, field_name) DO UPDATE"
        " SET value = excluded.value, is_encrypted = excluded.is_encrypted", upserts)

    # Defaults apply after merging, to fields still null. INSERT OR IGNORE keeps any
    # real value; a later non-null row simply overwrites the default.
    defaults = []
    for f in schema.fields:
        if f.default is None:
            continue
        dv = parse_value(f, str(f.default).strip()) if str(f.default).strip() else None
        if dv is None:
            dv = str(f.default)
        enc, flag = _store(vault, f, dv)
        defaults.extend((lid, f.name, enc, flag) for lid in touched)
    conn.executemany(
        "INSERT OR IGNORE INTO lead_fields (lead_id, field_name, value, is_encrypted)"
        " VALUES (?,?,?,?)", defaults)
    conn.executemany("INSERT INTO rejections VALUES (?,?,?,?,?)", rejections)
    return stats


# ====================================================================== events
@dataclass
class EventStats:
    total: int = 0
    malformed: int = 0
    duplicates: int = 0
    inserted: int = 0


def _as_str(v: object) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def ingest_event_file(conn: sqlite3.Connection, vault: PiiVault, path: Path,
                      file_id: int, seen: Set[str]) -> EventStats:
    stats = EventStats()
    batch: List[tuple] = []
    phone_cache: Dict[str, Optional[str]] = {}

    def flush():
        if batch:
            conn.executemany(
                "INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash,"
                " occurred_at_utc, occurred_us, disposition_code, payload, file_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
            stats.inserted += len(batch)
            batch.clear()

    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.decode("utf-8", errors="replace").strip().lstrip("\ufeff")
            if not line:
                continue
            stats.total += 1
            try:
                obj = json.loads(line)
            except ValueError:
                stats.malformed += 1
                continue
            if not isinstance(obj, dict):
                stats.malformed += 1
                continue
            event_id = _as_str(obj.get("event_id"))
            if event_id is None:
                stats.malformed += 1
                continue
            if event_id in seen:
                stats.duplicates += 1
                continue
            # An event with no usable timestamp cannot be placed on the timeline: malformed.
            ts = nz.parse_timestamp(obj.get("occurred_at"))
            if ts is None:
                stats.malformed += 1
                continue
            seen.add(event_id)

            phone_raw = _as_str(obj.get("phone"))
            phash = None
            if phone_raw is not None:
                if phone_raw not in phone_cache:
                    canon = nz.normalize_phone(phone_raw)
                    phone_cache[phone_raw] = vault.phone_hash(canon) if canon else None
                phash = phone_cache[phone_raw]

            payload = obj.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            batch.append((
                event_id, _as_str(obj.get("event_type")), _as_str(obj.get("workflow_id")),
                _as_str(obj.get("call_id")), phash,
                ts.isoformat(), nz.to_micros(ts), _as_str(payload.get("disposition_code")),
                json.dumps(payload, sort_keys=True, separators=(",", ":")), file_id))
            if len(batch) >= EVENT_BATCH:
                flush()
    flush()
    return stats


# ================================================================ orchestration
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sync_reference_data(conn: sqlite3.Connection, schemas: Dict[str, Schema],
                        dispositions_csv: Path) -> None:
    """Persist workflow schemas and the disposition catalogue so reports need only the DB."""
    conn.execute("BEGIN")
    try:
        seen = set()
        for s in schemas.values():
            if s.workflow_id in seen:
                continue
            seen.add(s.workflow_id)
            ref = s.reference_field
            conn.execute(
                "INSERT INTO workflows (workflow_id, reference_field, phone_field, schema_json)"
                " VALUES (?,?,?,?) ON CONFLICT (workflow_id) DO UPDATE SET"
                " reference_field = excluded.reference_field, phone_field = excluded.phone_field,"
                " schema_json = excluded.schema_json",
                (s.workflow_id, ref.name if ref else None, s.phone_field.name,
                 json.dumps(s.raw, sort_keys=True)))
        conn.execute("DELETE FROM dispositions")
        for wf, code, term, succ in load_dispositions(dispositions_csv):
            for target in ([wf] if wf else sorted(seen)):
                if target in seen:
                    conn.execute("INSERT OR REPLACE INTO dispositions VALUES (?,?,?,?)",
                                 (target, code, int(term), int(succ)))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def plan_drop(drop_dir: Path) -> List[Tuple[str, Optional[str], Path]]:
    """Deterministic order: lead files (workflow, filename) then event files (filename)."""
    plan: List[Tuple[str, Optional[str], Path]] = []
    leads_root = drop_dir / "leads"
    if leads_root.is_dir():
        for wf_dir in sorted(p for p in leads_root.iterdir() if p.is_dir()):
            for f in sorted(wf_dir.glob("*.csv")):
                plan.append(("leads", wf_dir.name, f))
    events_root = drop_dir / "events"
    if events_root.is_dir():
        for f in sorted(events_root.glob("*.jsonl")):
            plan.append(("events", None, f))
    return plan


def ingest_drop(conn: sqlite3.Connection, vault: PiiVault, drop_dir: Path,
                schemas: Dict[str, Schema], log: Callable[[str], None] = print) -> Dict[str, int]:
    drop_dir = drop_dir.resolve()
    if not drop_dir.is_dir():
        raise IngestError(f"not a directory: {drop_dir}")
    drop_name = drop_dir.name
    plan = plan_drop(drop_dir)

    # Validate everything up front so a bad drop changes nothing.
    known: Dict[str, Optional[str]] = {
        r[0]: r[1] for r in conn.execute("SELECT drop_name || '/' || relpath, sha256 FROM ingest_files")}
    todo = []
    result = {"ingested": 0, "skipped": 0}
    for kind, wf, path in plan:
        rel = path.relative_to(drop_dir).as_posix()
        sha = _sha256(path)
        prev = known.get(f"{drop_name}/{rel}")
        if prev is not None:
            if prev != sha:
                raise IngestError(
                    f"{drop_name}/{rel} was ingested before with different content; "
                    "refusing to double-count. Use a fresh --db for a different dataset.")
            result["skipped"] += 1
            log(f"  skip   {drop_name}/{rel} (already ingested)")
            continue
        if kind == "leads" and wf not in schemas:
            raise IngestError(f"no schema for workflow '{wf}' ({drop_name}/{rel})")
        todo.append((kind, wf, path, rel, sha))

    seen_events: Optional[Set[str]] = None
    identity_cache: Dict[str, Dict[str, int]] = {}
    for kind, wf, path, rel, sha in todo:
        conn.execute("BEGIN")
        try:
            batch_id = path.stem if kind == "leads" else None
            file_id = conn.execute(
                "INSERT INTO ingest_files (drop_name, relpath, kind, workflow_id, batch_id, sha256, ingested_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (drop_name, rel, kind, wf, batch_id, sha,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid
            if kind == "leads":
                schema = schemas[wf]
                wid = schema.workflow_id
                if wid not in identity_cache:
                    identity_cache[wid] = {h: i for h, i in conn.execute(
                        "SELECT phone_hash, lead_id FROM leads WHERE workflow_id = ?", (wid,))}
                st = ingest_lead_file(conn, vault, schema, path, file_id, batch_id, identity_cache[wid])
                conn.execute(
                    "UPDATE ingest_files SET lead_rows_total=?, lead_rows_rejected=?,"
                    " duplicate_lead_rows_merged=?, field_warnings=? WHERE file_id=?",
                    (st.rows_total, st.rejected, st.merged, st.warnings, file_id))
                log(f"  leads  {drop_name}/{rel}: rows={st.rows_total} rejected={st.rejected} "
                    f"merged={st.merged} warnings={st.warnings}")
            else:
                if seen_events is None:
                    seen_events = {r[0] for r in conn.execute("SELECT event_id FROM events")}
                st = ingest_event_file(conn, vault, path, file_id, seen_events)
                conn.execute(
                    "UPDATE ingest_files SET events_total=?, malformed_events=?,"
                    " duplicate_events_dropped=? WHERE file_id=?",
                    (st.total, st.malformed, st.duplicates, file_id))
                log(f"  events {drop_name}/{rel}: lines={st.total} stored={st.inserted} "
                    f"malformed={st.malformed} duplicates={st.duplicates}")
            conn.execute("COMMIT")
            result["ingested"] += 1
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result
