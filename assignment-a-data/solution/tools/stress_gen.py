"""Synthetic messy data at scale, only for timing the pipeline (NOT the official generator).

    python tools/stress_gen.py --leads 140000 --events 600000 --out /tmp/stress
"""
import argparse, json, random
from pathlib import Path

SCHEMA = {"workflow_id": "motor_renewal", "fields": [
    {"name": "customer_name", "type": "string", "required": True, "pii": True},
    {"name": "phone", "type": "phone", "required": True, "pii": True, "aliases": ["mobile"]},
    {"name": "policy_number", "type": "string", "required": True, "reference": True},
    {"name": "email", "type": "string", "pii": True},
    {"name": "renewal_date", "type": "date"}, {"name": "premium", "type": "decimal"},
    {"name": "preferred_language", "type": "enum", "values": ["en", "hi"], "default": "en"}]}
FMT = ["{p}", "+91 {a} {b}", "91-{a}-{b}", "0{p}", "{a}-{b}", "+91{p}"]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--leads", type=int, default=140000)
    ap.add_argument("--events", type=int, default=600000); ap.add_argument("--out", default="/tmp/stress")
    ap.add_argument("--seed", type=int, default=1); a = ap.parse_args()
    rnd = random.Random(a.seed); out = Path(a.out)
    (out / "schemas").mkdir(parents=True, exist_ok=True)
    (out / "schemas/motor_renewal.json").write_text(json.dumps(SCHEMA))
    (out / "schemas/dispositions.csv").write_text(
        "workflow_id,disposition_code,is_terminal,is_success\nmotor_renewal,renewed,true,true\n"
        "motor_renewal,callback,false,false\nmotor_renewal,not_interested,true,false\n")
    phones = [str(rnd.choice("6789")) + "".join(rnd.choice("0123456789") for _ in range(9)) for _ in range(a.leads)]
    for drop, (lo, hi) in {"drop_1": (0, a.leads // 2), "drop_2": (a.leads // 2, a.leads)}.items():
        d = out / "data" / drop / "leads/motor_renewal"; d.mkdir(parents=True, exist_ok=True)
        n = 1 if drop == "drop_1" else 10
        for start in range(lo, hi, 25000):
            with open(d / f"batch_{n:03d}.csv", "w") as f:
                f.write("customer_name,mobile,policy_number,email,renewal_date,premium,preferred_language\n")
                for i in range(start, min(start + 25000, hi)):
                    p = phones[i]; fmt = rnd.choice(FMT).format(p=p, a=p[:5], b=p[5:])
                    if rnd.random() < .03: fmt = fmt[:6]
                    f.write(f"Cust {i},{fmt},POL{i:07d},c{i}@x.in,{rnd.choice(['01/12/2026','2026-12-01','31/02/2026','01-Dec-2026'])},"
                            f"\"Rs. {rnd.randint(500,90000):,}\",{rnd.choice(['en','HI','','xx'])}\n")
                    if rnd.random() < .03:   # duplicate row in same batch
                        f.write(f"Cust {i}b,{p},POL{i:07d},,,,hi\n")
            n += 1 if drop == "drop_1" else 1
    ev = {"drop_1": [], "drop_2": []}; eid = call = 0
    codes = ["renewed", "callback", "not_interested", "bogus"]
    while eid < a.events:
        i = rnd.randrange(a.leads) if rnd.random() > .05 else None
        ph = phones[i] if i is not None else "9" + "".join(rnd.choice("0123456789") for _ in range(9))
        call += 1; t = 1_790_000_000 + rnd.randrange(0, 40 * 86400); drop = ev["drop_1"] if rnd.random() < .5 else ev["drop_2"]
        def mk(typ, dt, payload, phone=True):
            nonlocal eid
            eid += 1
            e = {"event_id": f"evt_{eid:08d}", "event_type": typ, "workflow_id": "motor_renewal", "call_id": f"call_{call:08d}",
                 "occurred_at": dt, "payload": payload}
            if phone: e["phone"] = ph
            return e
        from datetime import datetime, timezone, timedelta
        iso = lambda s, kind=0: (datetime.fromtimestamp(s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if kind == 0 else
              datetime.fromtimestamp(s, timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S"))
        k = rnd.randrange(2)
        evs = [mk("call.initiated", iso(t, k), {})]
        if rnd.random() < .3: evs.append(mk("call.telephony_failed", iso(t + 3, k), {"reason": "busy"}))
        else:
            evs += [mk("call.connected", iso(t + 5, k), {}), mk("call.ended", iso(t + 60, k), {"duration_sec": 55, "end_reason": "hangup"}),
                    mk("disposition.set", iso(t + 61, k), {"disposition_code": rnd.choice(codes)})]
            if rnd.random() < .1: evs.append(mk("disposition.override", iso(t + 900, k), {"disposition_code": "renewed"}, phone=False))
        for e in evs:
            drop.append(json.dumps(e))
            if rnd.random() < .02: drop.append(json.dumps(e))        # duplicate
            if rnd.random() < .003: drop.append("{broken json")
    for drop, lines in ev.items():
        rnd.shuffle(lines)
        d = out / "data" / drop / "events"; d.mkdir(parents=True, exist_ok=True)
        (d / ("events_0001.jsonl" if drop == "drop_1" else "events_1001.jsonl")).write_text("\n".join(lines) + "\n")

main()
