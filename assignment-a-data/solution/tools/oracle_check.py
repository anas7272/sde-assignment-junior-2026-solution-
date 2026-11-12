"""Independent in-memory re-implementation of the spec, used to cross-check the SQL pipeline.

    python tools/oracle_check.py <data_root_containing_drop_1_drop_2> <schemas_dir> <reports_dir>
Shares only the leaf normalisers with the pipeline; linking/derivation/reporting are re-done
from the raw files with plain dicts (no SQLite, no views).
"""
import csv, json, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ledger import normalize as nz
from ledger.ingest import evaluate_row
from ledger.schema import load_schemas, load_dispositions

root, sdir, rep = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
schemas = load_schemas(sdir)
cat = {}
for workflow, code, terminal, success in load_dispositions(sdir / "dispositions.csv"):
    for target in ([workflow] if workflow else sorted({s.workflow_id for s in schemas.values()})):
        cat[(target, code)] = (terminal, success)
leads, q = {}, defaultdict(int); rej = defaultdict(int); rows_rej = []
for drop in sorted(p for p in (root / "data").iterdir() if p.is_dir()):
    for wfd in sorted((drop / "leads").iterdir()):
        sch = schemas[wfd.name]; lk = sch.header_lookup()
        for f in sorted(wfd.glob("*.csv")):
            rd = list(csv.reader(open(f, newline="", encoding="utf-8-sig")))
            hdr = {}
            for i, h in enumerate(rd[0]):
                fl = lk.get(nz.normalize_header(h))
                if fl and fl.name not in hdr: hdr[fl.name] = i
            n = 0
            for row in rd[1:]:
                if all(not c.strip() for c in row): continue
                n += 1; q["lead_rows_total"] += 1
                raws = {k: (row[i].strip() or None) if i < len(row) else None for k, i in hdr.items()}
                reason, vals, w = evaluate_row(sch, raws)
                if reason:
                    q["lead_rows_rejected"] += 1; rej[reason] += 1; rows_rej.append((wfd.name, f.stem, n, reason)); continue
                q["field_warnings"] += w
                key = (wfd.name, vals[sch.phone_field.name])
                if key in leads:
                    q["duplicate_lead_rows_merged"] += 1
                    for k, v in vals.items():
                        if v is not None: leads[key][k] = v
                else:
                    leads[key] = {k: v for k, v in vals.items() if v is not None}; leads[key]["_batch"] = f.stem
    for f in sorted((drop / "events").glob("*.jsonl")):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line: continue
            q["events_total"] += 1
            try: e = json.loads(line)
            except ValueError: q["malformed_events"] += 1; continue
            if not isinstance(e, dict) or not e.get("event_id"): q["malformed_events"] += 1; continue
            q.setdefault("_ev", []).append(e)
seen, events = set(), []
for e in q.pop("_ev"):
    if e["event_id"] in seen: q["duplicate_events_dropped"] += 1; continue
    seen.add(e["event_id"]); events.append(e)
q.pop("_seen", None)
call_lead = {}
for e in events:
    if e["event_type"] != "disposition.override":
        ph = nz.normalize_phone(e.get("phone"))
        if ph and (e["workflow_id"], ph) in leads:
            call_lead.setdefault(e["call_id"], (e["workflow_id"], ph))
calls = defaultdict(lambda: {"init": [], "conn": False, "fail": False, "set": [], "ov": []})
for i, e in enumerate(events):
    t = e["event_type"]
    if t == "disposition.override": lead = call_lead.get(e["call_id"])
    else:
        ph = nz.normalize_phone(e.get("phone")); lead = (e["workflow_id"], ph) if ph and (e["workflow_id"], ph) in leads else None
    if lead is None: q["orphan_events"] += 1; continue
    if t.startswith("disposition.") and (lead[0], (e.get("payload") or {}).get("disposition_code")) not in cat:
        q["unknown_disposition_events"] += 1; continue
    ts = nz.to_micros(nz.parse_timestamp(e["occurred_at"])); c = calls[(lead, e["call_id"])]
    if t == "call.initiated": c["init"].append(ts)
    elif t == "call.connected": c["conn"] = True
    elif t == "call.telephony_failed": c["fail"] = True
    elif t == "disposition.set": c["set"].append((ts, i, e["payload"]["disposition_code"]))
    elif t == "disposition.override": c["ov"].append((ts, i, e["payload"]["disposition_code"]))
daily = defaultdict(lambda: [0, 0, set()]); per_lead = defaultdict(list)
for (lead, cid), c in calls.items():
    if not c["init"]: continue
    eff = max(c["ov"])[2] if c["ov"] else (max(c["set"])[2] if c["set"] else None)
    ini = min(c["init"]); d = nz.from_micros(ini).astimezone(nz.IST).date().isoformat()
    per_lead[lead].append((ini, cid, c["conn"], c["fail"], eff))
    s = daily[(lead[0], d)]; s[0] += 1; s[1] += c["conn"]
    if eff and cat[(lead[0], eff)][1]: s[2].add(lead)
mine = json.loads((rep / "data_quality.json").read_text())
ok = True
for k, v in q.items():
    if mine[k] != v: ok = False; print("MISMATCH data_quality", k, "oracle", v, "pipeline", mine[k])
if mine["rejections_by_reason"] != dict(rej): ok = False; print("MISMATCH rejections_by_reason")
exp_daily = [[w, d, str(t), str(c), f"{c/t:.4f}", str(len(s))] for (w, d), (t, c, s) in sorted(daily.items())]
if list(csv.reader(open(rep / "daily_overview.csv")))[1:] != exp_daily: ok = False; print("MISMATCH daily_overview")
buckets = defaultdict(lambda: defaultdict(int))
for key, ld in leads.items():
    cs = per_lead.get(key, [])
    if not cs: b = "yet_to_reach_out"
    elif any(c[4] and cat[(key[0], c[4])][0] for c in cs): b = "completed"
    elif all(c[3] for c in cs): b = "in_progress_telephony_failed"
    else: b = "in_progress_dialed"
    buckets[(key[0], ld["_batch"])]["total_leads"] += 1; buckets[(key[0], ld["_batch"])][b] += 1
exp_b = [[w, b, str(v["total_leads"]), str(v["yet_to_reach_out"]), str(v["in_progress_telephony_failed"]),
          str(v["in_progress_dialed"]), str(v["completed"])] for (w, b), v in sorted(buckets.items())]
if list(csv.reader(open(rep / "batch_summary.csv")))[1:] != exp_b: ok = False; print("MISMATCH batch_summary")
exp_r = [list(map(str, r)) for r in sorted(rows_rej)]
if list(csv.reader(open(rep / "rejections.csv")))[1:] != exp_r: ok = False; print("MISMATCH rejections")
exp_leads = []
for key, ld in sorted(
        leads.items(),
        key=lambda item: (item[0][0],
                          item[1].get(schemas[item[0][0]].reference_field.name, ""))):
    cs = sorted(per_lead.get(key, []), key=lambda c: (c[0], c[1]))
    if not cs:
        bucket = "yet_to_reach_out"
    elif any(c[4] and cat[(key[0], c[4])][0] for c in cs):
        bucket = "completed"
    elif all(c[3] for c in cs):
        bucket = "in_progress_telephony_failed"
    else:
        bucket = "in_progress_dialed"
    last = max(cs, key=lambda c: (c[0], c[1])) if cs else None
    current = next((c[4] for c in reversed(cs) if c[4]), "")
    ref_name = schemas[key[0]].reference_field.name
    exp_leads.append([
        key[0], ld.get(ref_name, ""), ld["_batch"], "+91******" + key[1][-4:], bucket,
        str(len(cs)), str(sum(c[2] for c in cs)),
        nz.from_micros(last[0]).astimezone(nz.IST).isoformat(timespec="seconds") if last else "",
        current or "",
        str(any(c[4] and cat[(key[0], c[4])][1] for c in cs)).lower(),
    ])
got_leads = list(csv.reader(open(rep / "lead_report.csv")))[1:]
if got_leads != exp_leads:
    ok = False
    print("MISMATCH lead_report: oracle and pipeline rows differ")
print("ORACLE AGREES ON ALL FIVE REPORTS" if ok else "ORACLE DISAGREES")
