"""Reports are computed from the full ledger state every time, so "drop_1 then
drop_2" and "everything at once" are identical by construction, and late-arriving
leads automatically claim their earlier events."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import derive
from .normalize import IST, from_micros
from .pii import PiiVault


@dataclass
class LeadRec:
    lead_id: int
    workflow_id: str
    batch_id: str
    last4: str
    external_ref: str = ""
    calls: List[derive.CallFacts] = field(default_factory=list)


def _load_state(conn: sqlite3.Connection, vault: PiiVault):
    catalogue: Dict[Tuple[str, str], Tuple[bool, bool]] = {
        (wf, code): (bool(t), bool(s)) for wf, code, t, s in conn.execute(
            "SELECT workflow_id, code, is_terminal, is_success FROM dispositions")}

    leads: Dict[int, LeadRec] = {}
    for lid, wf, batch, last4 in conn.execute(
            "SELECT lead_id, workflow_id, batch_id, phone_last4 FROM leads ORDER BY lead_id"):
        leads[lid] = LeadRec(lid, wf, batch, last4)

    for wf, ref_field in conn.execute("SELECT workflow_id, reference_field FROM workflows"):
        if not ref_field:
            continue
        for lid, value, enc in conn.execute(
                "SELECT f.lead_id, f.value, f.is_encrypted FROM lead_fields f"
                " JOIN leads l USING (lead_id) WHERE l.workflow_id = ? AND f.field_name = ?",
                (wf, ref_field)):
            leads[lid].external_ref = vault.decrypt(value) if enc else value

    # Group linked events per call. Orphans (lead_id NULL) never enter any report.
    per_call: Dict[Tuple[str, str], List[tuple]] = defaultdict(list)
    unknown_disposition = 0
    for seq, etype, call_id, us, code, lead_id in conn.execute(
            "SELECT seq, event_type, call_id, occurred_us, disposition_code, lead_id"
            " FROM v_event_links ORDER BY seq"):
        if lead_id is None:
            continue
        wf = leads[lead_id].workflow_id
        etype = etype or ""
        if etype.startswith("disposition."):
            if code is None or (wf, code) not in catalogue:
                unknown_disposition += 1  # counted and ignored
                continue
        if call_id is None:
            continue
        per_call[(wf, call_id)].append((etype, us, seq, code, lead_id))

    # Evaluate each call. A call exists only if its call.initiated is present and linked.
    for (wf, call_id), evs in per_call.items():
        inits = [e for e in evs if e[0] == "call.initiated"]
        if not inits:
            continue
        first = min(inits, key=lambda e: (e[1], e[2]))
        lead_id = first[4]
        mine = [e for e in evs if e[4] == lead_id]
        sets = [(e[1], e[2], e[3]) for e in mine if e[0] == "disposition.set"]
        overs = [(e[1], e[2], e[3]) for e in mine if e[0] == "disposition.override"]
        call = derive.CallFacts(
            call_id=call_id, initiated_us=first[1],
            connected=any(e[0] == "call.connected" for e in mine),
            telephony_failed=any(e[0] == "call.telephony_failed" for e in mine),
            effective=derive.effective_disposition(sets, overs))
        leads[lead_id].calls.append(call)
    return catalogue, leads, unknown_disposition


def _write_csv(path: Path, header: List[str], rows: List[List[object]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _b(v: bool) -> str:
    return "true" if v else "false"


def generate_reports(conn: sqlite3.Connection, vault: PiiVault, out_dir: Path) -> Dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)
    catalogue, leads, unknown = _load_state(conn, vault)

    def is_terminal(wf: str):
        return lambda code: catalogue[(wf, code)][0]

    def is_success(wf: str, code: Optional[str]) -> bool:
        return code is not None and catalogue[(wf, code)][1]

    lead_rows = []
    batch_counts: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    daily: Dict[Tuple[str, str], List] = defaultdict(lambda: [0, 0, set()])  # total, connected, success lead ids

    for lead in sorted(leads.values(), key=lambda l: (l.workflow_id, l.external_ref, l.lead_id)):
        wf = lead.workflow_id
        bucket = derive.lead_bucket(lead.calls, is_terminal(wf))
        batch_counts[(wf, lead.batch_id)]["total"] += 1
        batch_counts[(wf, lead.batch_id)][bucket] += 1

        for c in lead.calls:
            d = from_micros(c.initiated_us).astimezone(IST).date().isoformat()
            slot = daily[(wf, d)]
            slot[0] += 1
            slot[1] += int(c.connected)
            if is_success(wf, c.effective):
                slot[2].add(lead.lead_id)

        last_call = max(lead.calls, key=lambda c: (c.initiated_us, c.call_id)) if lead.calls else None
        lead_rows.append([
            wf, lead.external_ref, lead.batch_id, "+91******" + lead.last4, bucket,
            len(lead.calls), sum(c.connected for c in lead.calls),
            from_micros(last_call.initiated_us).astimezone(IST).isoformat(timespec="seconds") if last_call else "",
            derive.current_disposition(lead.calls) or "",
            _b(any(is_success(wf, c.effective) for c in lead.calls))])

    _write_csv(out_dir / "lead_report.csv",
               ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
                "connected_calls", "last_call_at", "current_disposition", "ever_success"], lead_rows)

    _write_csv(out_dir / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out",
                "in_progress_telephony_failed", "in_progress_dialed", "completed"],
               [[wf, batch, c["total"], c[derive.BUCKET_YET], c[derive.BUCKET_TELEPHONY_FAILED],
                 c[derive.BUCKET_DIALED], c[derive.BUCKET_COMPLETED]]
                for (wf, batch), c in sorted(batch_counts.items())])

    _write_csv(out_dir / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
               [[wf, d, t, c, f"{c / t:.4f}", len(s)] for (wf, d), (t, c, s) in sorted(daily.items())])

    rej = conn.execute(
        "SELECT workflow_id, batch_id, row_number, reason FROM rejections"
        " ORDER BY workflow_id, batch_id, row_number").fetchall()
    _write_csv(out_dir / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"],
               [list(r) for r in rej])

    sums = conn.execute(
        "SELECT COALESCE(SUM(lead_rows_total),0), COALESCE(SUM(lead_rows_rejected),0),"
        " COALESCE(SUM(duplicate_lead_rows_merged),0), COALESCE(SUM(field_warnings),0),"
        " COALESCE(SUM(events_total),0), COALESCE(SUM(malformed_events),0),"
        " COALESCE(SUM(duplicate_events_dropped),0) FROM ingest_files").fetchone()
    orphans = conn.execute("SELECT COUNT(*) FROM v_event_links WHERE lead_id IS NULL").fetchone()[0]
    by_reason: Dict[str, int] = {}
    for r in conn.execute("SELECT reason, COUNT(*) FROM rejections GROUP BY reason ORDER BY reason"):
        by_reason[r[0]] = r[1]
    quality = {
        "lead_rows_total": sums[0], "lead_rows_rejected": sums[1],
        "rejections_by_reason": by_reason, "duplicate_lead_rows_merged": sums[2],
        "field_warnings": sums[3], "events_total": sums[4], "malformed_events": sums[5],
        "duplicate_events_dropped": sums[6], "orphan_events": orphans,
        "unknown_disposition_events": unknown,
    }
    (out_dir / "data_quality.json").write_text(json.dumps(quality, indent=2) + "\n", encoding="utf-8")
    return quality
