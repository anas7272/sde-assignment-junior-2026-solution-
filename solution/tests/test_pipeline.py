"""End-to-end tests on a small hand-computed fixture (expected values derived from the spec)."""
import csv
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ledger import db as dbmod
from ledger.cli import main
from ledger.ingest import ingest_drop, sync_reference_data
from ledger.pii import PiiVault, load_vault
from ledger.report import generate_reports
from ledger.schema import load_schemas

SCHEMA = {
    "workflow_id": "motor_renewal",
    "fields": [
        {"name": "customer_name", "type": "string", "required": True, "pii": True, "aliases": ["name"]},
        {"name": "phone", "type": "phone", "required": True, "pii": True, "aliases": ["mobile number", "mobile"]},
        {"name": "policy_number", "type": "string", "required": True, "reference": True, "aliases": ["policy no"]},
        {"name": "renewal_date", "type": "date"},
        {"name": "premium", "type": "decimal"},
        {"name": "preferred_language", "type": "enum", "values": ["en", "hi"], "default": "en"},
    ],
}
DISPOSITIONS = ("workflow_id,disposition_code,is_terminal,is_success\n"
                "motor_renewal,renewed,true,true\n"
                "motor_renewal,callback,false,false\n"
                "motor_renewal,not_interested,true,false\n")

BATCH_001 = """customer_name,phone,policy_number,renewal_date,premium,preferred_language,extra
Asha,98765 43210,P001,01/12/2026,"Rs. 1,200.50",HI,x
Bela,+91-91234-56789,P002,31/02/2026,₹500,en,x
,9876500001,P003,,,,x
Cara,12345,P004,,,,x
 , ,,,,,
Dev,09988776655,P005,2026-12-05,abc,fr,x
Asha Dup,919876543210,P001b,,,hi,x
"""
BATCH_003 = """Customer_Name,Mobile Number,Policy_No,preferred language,Renewal Date
Eve,9000000001,P006,,
Devi,99887 76655,P005,hi,
"""
E = lambda **k: json.dumps(k)
EV1 = [
    E(event_id="evt_1", event_type="call.initiated", workflow_id="motor_renewal", call_id="call_1", occurred_at="2026-10-05T10:00:00Z", phone="98765-43210", payload={}),
    E(event_id="evt_2", event_type="call.connected", workflow_id="motor_renewal", call_id="call_1", occurred_at="2026-10-05T10:00:20Z", phone="+91 9876543210", payload={}),
    E(event_id="evt_3", event_type="disposition.set", workflow_id="motor_renewal", call_id="call_1", occurred_at="2026-10-05T10:05:00Z", phone="919876543210", payload={"disposition_code": "callback"}),
    E(event_id="evt_4", event_type="disposition.override", workflow_id="motor_renewal", call_id="call_1", occurred_at="2026-10-05T10:10:00Z", payload={"disposition_code": "renewed", "overridden_by": "sup", "reason": "x"}),
    E(event_id="evt_5", event_type="call.initiated", workflow_id="motor_renewal", call_id="call_2", occurred_at="2026-10-06 09:00:00", phone="9123456789", payload={}),
    E(event_id="evt_6", event_type="call.telephony_failed", workflow_id="motor_renewal", call_id="call_2", occurred_at="2026-10-06 09:00:05", phone="9123456789", payload={"reason": "carrier_rejected"}),
    E(event_id="evt_1", event_type="call.initiated", workflow_id="motor_renewal", call_id="call_1", occurred_at="2026-10-05T10:00:00Z", phone="98765-43210", payload={}),
    "{not json",
    '{"event_type": "call.initiated"}',
    "",
    E(event_id="evt_7", event_type="call.initiated", workflow_id="motor_renewal", call_id="call_3", occurred_at="2026-10-07T20:00:00+05:30", phone="9000000001", payload={}),
    E(event_id="evt_8", event_type="disposition.set", workflow_id="motor_renewal", call_id="call_3", occurred_at="2026-10-07T20:05:00+05:30", phone="9000000001", payload={"disposition_code": "bogus"}),
    E(event_id="evt_9", event_type="call.initiated", workflow_id="motor_renewal", call_id="call_x", occurred_at="2026-10-07T21:00:00Z", phone="9111111111", payload={}),
    E(event_id="evt_10", event_type="disposition.override", workflow_id="motor_renewal", call_id="call_x", occurred_at="2026-10-07T22:00:00Z", payload={"disposition_code": "renewed"}),
    E(event_id="evt_11", event_type="call.initiated", workflow_id="motor_renewal", call_id="call_4", occurred_at="2026-10-08T01:00:00Z", phone="09988776655", payload={}),
    E(event_id="evt_12", event_type="disposition.set", workflow_id="motor_renewal", call_id="call_4", occurred_at="2026-10-08T01:05:00Z", phone="09988776655", payload={"disposition_code": "callback"}),
]
EV2 = [
    E(event_id="evt_13", event_type="call.connected", workflow_id="motor_renewal", call_id="call_3", occurred_at="2026-10-07T14:31:00Z", phone="9000000001", payload={}),
    E(event_id="evt_14", event_type="disposition.set", workflow_id="motor_renewal", call_id="call_3", occurred_at="2026-10-07T14:40:00Z", phone="9000000001", payload={"disposition_code": "not_interested"}),
    E(event_id="evt_5", event_type="call.initiated", workflow_id="motor_renewal", call_id="call_2", occurred_at="2026-10-06 09:00:00", phone="9123456789", payload={}),
    E(event_id="evt_16", event_type="call.initiated", workflow_id="motor_renewal", call_id="call_5", occurred_at="2026-10-09T10:00:00Z", phone="9123456789", payload={}),
    E(event_id="evt_17", event_type="call.telephony_failed", workflow_id="motor_renewal", call_id="call_5", occurred_at="2026-10-09T10:00:03Z", phone="9123456789", payload={"reason": "busy"}),
]


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.reader(fh))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.schemas = self.tmp / "schemas"
        write(self.schemas / "motor_renewal.json", json.dumps(SCHEMA))
        write(self.schemas / "dispositions.csv", DISPOSITIONS)
        d1, d2 = self.tmp / "data" / "drop_1", self.tmp / "data" / "drop_2"
        write(d1 / "leads/motor_renewal/batch_001.csv", BATCH_001)
        write(d1 / "events/events_0001.jsonl", "\n".join(EV1) + "\n")
        write(d2 / "leads/motor_renewal/batch_003.csv", BATCH_003)
        write(d2 / "events/events_1001.jsonl", "\n".join(EV2) + "\n")
        self.d1, self.d2 = d1, d2

    def run_cli(self, db, *args):
        return main(["--db", str(self.tmp / db), *args])

    def ingest(self, db, *drops):
        for d in drops:
            self.assertEqual(self.run_cli(db, "ingest", str(d), "--schemas", str(self.schemas)), 0)

    def report(self, db, out):
        self.assertEqual(self.run_cli(db, "report", "--out", str(self.tmp / out)), 0)
        return self.tmp / out

    # ------------------------------------------------------------------ core
    def test_final_reports_match_hand_computed_expectation(self):
        self.ingest("a.db", self.d1, self.d2)
        out = self.report("a.db", "out")
        self.assertEqual(read_csv(out / "batch_summary.csv"), [
            ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"],
            ["motor_renewal", "batch_001", "3", "0", "1", "1", "1"],
            ["motor_renewal", "batch_003", "1", "0", "0", "0", "1"]])
        self.assertEqual(read_csv(out / "daily_overview.csv"), [
            ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
            ["motor_renewal", "2026-10-05", "1", "1", "1.0000", "1"],
            ["motor_renewal", "2026-10-06", "1", "0", "0.0000", "0"],
            ["motor_renewal", "2026-10-07", "1", "1", "1.0000", "0"],
            ["motor_renewal", "2026-10-08", "1", "0", "0.0000", "0"],
            ["motor_renewal", "2026-10-09", "1", "0", "0.0000", "0"]])
        self.assertEqual(read_csv(out / "lead_report.csv"), [
            ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"],
            ["motor_renewal", "P001b", "batch_001", "+91******3210", "completed", "1", "1", "2026-10-05T15:30:00+05:30", "renewed", "true"],
            ["motor_renewal", "P002", "batch_001", "+91******6789", "in_progress_telephony_failed", "2", "0", "2026-10-09T15:30:00+05:30", "", "false"],
            ["motor_renewal", "P005", "batch_001", "+91******6655", "in_progress_dialed", "1", "0", "2026-10-08T06:30:00+05:30", "callback", "false"],
            ["motor_renewal", "P006", "batch_003", "+91******0001", "completed", "1", "1", "2026-10-07T20:00:00+05:30", "not_interested", "false"]])
        self.assertEqual(read_csv(out / "rejections.csv"), [
            ["workflow_id", "batch_id", "row_number", "reason"],
            ["motor_renewal", "batch_001", "3", "missing_required:customer_name"],
            ["motor_renewal", "batch_001", "4", "invalid_phone"]])
        self.assertEqual(json.loads((out / "data_quality.json").read_text()), {
            "lead_rows_total": 8, "lead_rows_rejected": 2,
            "rejections_by_reason": {"invalid_phone": 1, "missing_required:customer_name": 1},
            "duplicate_lead_rows_merged": 2, "field_warnings": 3,
            "events_total": 20, "malformed_events": 2, "duplicate_events_dropped": 2,
            "orphan_events": 2, "unknown_disposition_events": 1})

    def test_late_lead_claims_earlier_orphans(self):
        self.ingest("a.db", self.d1)
        q1 = json.loads((self.report("a.db", "o1") / "data_quality.json").read_text())
        self.assertEqual(q1["orphan_events"], 4)          # evt_7, 8, 9, 10
        self.assertEqual(q1["unknown_disposition_events"], 0)
        self.ingest("a.db", self.d2)
        q2 = json.loads((self.report("a.db", "o2") / "data_quality.json").read_text())
        self.assertEqual(q2["orphan_events"], 2)          # evt_7, 8 now linked
        self.assertEqual(q2["unknown_disposition_events"], 1)

    def test_incremental_equals_all_at_once(self):
        self.ingest("inc.db", self.d1)
        self.report("inc.db", "mid")
        self.ingest("inc.db", self.d2)
        a = self.report("inc.db", "inc_out")
        self.ingest("once.db", self.d1, self.d2)
        b = self.report("once.db", "once_out")
        for name in ["batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv", "data_quality.json"]:
            self.assertEqual((a / name).read_bytes(), (b / name).read_bytes(), name)

    def test_reingest_is_a_no_op(self):
        self.ingest("a.db", self.d1, self.d2)
        before = self.report("a.db", "before")
        counts = lambda: [sqlite3.connect(self.tmp / "a.db").execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                          for t in ("leads", "lead_fields", "events", "rejections", "ingest_files")]
        c1 = counts()
        self.ingest("a.db", self.d1, self.d2, self.d1)
        self.assertEqual(c1, counts())
        after = self.report("a.db", "after")
        for name in ["batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv", "data_quality.json"]:
            self.assertEqual((before / name).read_bytes(), (after / name).read_bytes(), name)

    def test_changed_file_under_same_name_is_refused(self):
        self.ingest("a.db", self.d1)
        write(self.d1 / "events/events_0001.jsonl", EV1[0] + "\n")
        self.assertEqual(self.run_cli("a.db", "ingest", str(self.d1), "--schemas", str(self.schemas)), 2)

    def test_event_duplicate_check_precedes_timestamp_validation(self):
        events = self.tmp / "event-order" / "events" / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text("\n".join([
            "{not json",
            '{"event_type":"call.initiated"}',
            E(event_id="evt_duplicate", occurred_at="2026-10-05T10:00:00Z"),
            E(event_id="evt_duplicate", occurred_at="not-a-timestamp"),
            E(event_id="evt_timestamp", occurred_at="not-a-timestamp"),
            E(event_id="evt_timestamp", occurred_at="2026-10-05T10:01:00Z"),
        ]) + "\n", encoding="utf-8")

        self.assertEqual(self.run_cli(
            "event-order.db", "ingest", str(events.parents[1]), "--schemas", str(self.schemas)), 0)

        conn = sqlite3.connect(self.tmp / "event-order.db")
        try:
            stats = conn.execute(
                "SELECT events_total, malformed_events, duplicate_events_dropped "
                "FROM ingest_files").fetchone()
            self.assertEqual(stats, (6, 3, 1))
            stored_ids = {row[0] for row in conn.execute("SELECT event_id FROM events")}
            self.assertEqual(stored_ids, {"evt_duplicate", "evt_timestamp"})
        finally:
            conn.close()

    # ---------------------------------------------------------------- ledger/PII
    def test_ledger_is_append_only(self):
        self.ingest("a.db", self.d1)
        conn = dbmod.connect(str(self.tmp / "a.db"))
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("UPDATE events SET call_id = 'x'")
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM events")

    def test_no_plaintext_pii_at_rest_and_reference_roundtrips(self):
        self.ingest("a.db", self.d1, self.d2)
        conn = sqlite3.connect(self.tmp / "a.db")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        blob = b"".join(p.read_bytes() for p in self.tmp.glob("a.db*") if not p.name.endswith(".key"))
        for needle in [b"9876543210", b"98765", b"9123456789", b"Asha", b"Bela", b"Devi", b"Eve", b"9000000001"]:
            self.assertNotIn(needle, blob, needle)
        vault = load_vault(str(self.tmp / "a.db"))
        name_ct = conn.execute("SELECT value FROM lead_fields WHERE field_name='customer_name' LIMIT 1").fetchone()[0]
        self.assertIn(vault.decrypt(name_ct), {"Asha Dup", "Bela", "Devi", "Eve"})
        with self.assertRaises(ValueError):
            PiiVault(b"wrong-key-wrong-key-wrong-key-12").decrypt(name_ct)

    def test_phone_encrypted_even_if_schema_does_not_flag_pii(self):
        sch = json.loads(json.dumps(SCHEMA))
        for f in sch["fields"]:
            f.pop("pii", None)
        write(self.schemas / "motor_renewal.json", json.dumps(sch))
        self.ingest("a.db", self.d1)
        conn = sqlite3.connect(self.tmp / "a.db")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        blob = b"".join(p.read_bytes() for p in self.tmp.glob("a.db*") if not p.name.endswith(".key"))
        self.assertNotIn(b"9876543210", blob)
        self.assertNotIn(b"+919876543210", blob)

    def test_merge_semantics_and_defaults(self):
        self.ingest("a.db", self.d1, self.d2)
        conn = sqlite3.connect(self.tmp / "a.db")
        vault = load_vault(str(self.tmp / "a.db"))
        h = vault.phone_hash("+919988776655")
        lid, batch = conn.execute("SELECT lead_id, batch_id FROM leads WHERE phone_hash=?", (h,)).fetchone()
        self.assertEqual(batch, "batch_001")                       # first valid row's batch is permanent
        f = {r[0]: (vault.decrypt(r[1]) if r[2] else r[1]) for r in conn.execute(
            "SELECT field_name, value, is_encrypted FROM lead_fields WHERE lead_id=?", (lid,))}
        self.assertEqual(f["customer_name"], "Devi")               # overwritten by later non-null
        self.assertEqual(f["policy_number"], "P005")
        self.assertEqual(f["preferred_language"], "hi")            # default 'en' replaced by real value
        self.assertEqual(f["renewal_date"], "2026-12-05")          # later empty value does not erase
        self.assertNotIn("premium", f)                              # invalid -> null


if __name__ == "__main__":
    unittest.main()
