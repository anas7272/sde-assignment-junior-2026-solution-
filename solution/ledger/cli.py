"""Command line: ``python -m ledger ingest <drop> | report --out <dir>``."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import db as dbmod
from .ingest import IngestError, ingest_drop, sync_reference_data
from .pii import load_vault
from .report import generate_reports
from .schema import load_schemas

DEFAULT_DB = os.environ.get("LEDGER_DB", "ledger.db")


def _find_schema_dir(explicit: Optional[str], drop: Optional[Path]) -> Path:
    cands: List[Path] = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("LEDGER_SCHEMAS"):
        cands.append(Path(os.environ["LEDGER_SCHEMAS"]))
    cands += [Path("assignment-a-data/schemas"), Path("schemas")]
    if drop is not None:  # <repo>/data/drop_N  ->  <repo>/assignment-a-data/schemas
        d = drop.resolve()
        cands += [d.parent.parent / "assignment-a-data" / "schemas", d.parent.parent / "schemas"]
    for c in cands:
        if c.is_dir() and (c / "dispositions.csv").exists():
            return c
    raise IngestError("cannot find the schemas directory (needs *.json + dispositions.csv); "
                      "pass --schemas DIR or set LEDGER_SCHEMAS")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="ledger", description="Interaction ledger & reporting pipeline")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite file (default: $LEDGER_DB or ./ledger.db)")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("ingest", help="ingest one drop directory (idempotent)")
    pi.add_argument("drop", type=Path)
    pi.add_argument("--schemas", help="directory with <workflow>.json and dispositions.csv")
    pr = sub.add_parser("report", help="write the five report files")
    pr.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)

    try:
        vault = load_vault(args.db)
        conn = dbmod.connect(args.db)
        t0 = time.perf_counter()
        if args.cmd == "ingest":
            sdir = _find_schema_dir(args.schemas, args.drop)
            schemas = load_schemas(sdir)
            sync_reference_data(conn, schemas, sdir / "dispositions.csv")
            print(f"ingest {args.drop} (schemas: {sdir})")
            res = ingest_drop(conn, vault, args.drop, schemas)
            print(f"done: {res['ingested']} file(s) ingested, {res['skipped']} skipped "
                  f"in {time.perf_counter() - t0:.2f}s")
        else:
            q = generate_reports(conn, vault, args.out)
            print(f"wrote 5 reports to {args.out} in {time.perf_counter() - t0:.2f}s "
                  f"(orphans={q['orphan_events']}, rejected={q['lead_rows_rejected']})")
        return 0
    except IngestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
