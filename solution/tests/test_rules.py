import unittest
from datetime import timezone

from ledger import derive, normalize as nz
from ledger.derive import CallFacts


class PhoneTests(unittest.TestCase):
    def test_six_formats_all_canonicalise(self):
        for raw in ["9876543210", "+91 98765 43210", "91-98765-43210", "09876543210",
                    "(98765) 43210", "919876543210"]:
            self.assertEqual(nz.normalize_phone(raw), "+919876543210", raw)

    def test_invalid(self):
        for raw in ["", "12345", "5876543210", "98765432101", "0919876543210", "abc", None,
                    "+91 5876543210", "9199999999999"]:
            self.assertIsNone(nz.normalize_phone(raw), raw)

    def test_eleven_digits_not_starting_zero_invalid(self):
        self.assertIsNone(nz.normalize_phone("19876543210"))


class DateTests(unittest.TestCase):
    def test_formats_day_first(self):
        self.assertEqual(nz.parse_date("2026-12-01"), "2026-12-01")
        self.assertEqual(nz.parse_date("01/12/2026"), "2026-12-01")   # 1 Dec, not 12 Jan
        self.assertEqual(nz.parse_date("01-12-2026"), "2026-12-01")
        self.assertEqual(nz.parse_date("01-Dec-2026"), "2026-12-01")
        self.assertEqual(nz.parse_date("01-dEC-2026"), "2026-12-01")

    def test_invalid(self):
        for raw in ["31/02/2026", "2026-13-01", "13/13/2026", "01-Foo-2026", "2026/12/01", "x"]:
            self.assertIsNone(nz.parse_date(raw), raw)


class DecimalTests(unittest.TestCase):
    def test_prefixes_and_commas(self):
        for raw, exp in [("1200", "1200.00"), ("₹1,200.5", "1200.50"), ("Rs 1,23,456", "123456.00"),
                         ("rs. 10", "10.00"), ("INR10.005", "10.01"), ("inr 0", "0.00"), ("Rs.99.994", "99.99")]:
            self.assertEqual(nz.parse_decimal(raw), exp, raw)

    def test_invalid(self):
        for raw in ["-5", "abc", "Rs", "1.2.3", "$5", ""]:
            self.assertIsNone(nz.parse_decimal(raw), raw)


class HeaderTimestampTests(unittest.TestCase):
    def test_header(self):
        self.assertEqual(nz.normalize_header("  Mobile__Number\t"), "mobile number")
        self.assertEqual(nz.normalize_header("Policy _ No"), "policy no")

    def test_timestamps(self):
        z = nz.parse_timestamp("2026-10-05T10:00:00Z")
        off = nz.parse_timestamp("2026-10-05T15:30:00+05:30")
        naive = nz.parse_timestamp("2026-10-05 15:30:00")   # naive means IST
        self.assertEqual(z, off)
        self.assertEqual(z, naive)
        self.assertEqual(z.tzinfo, timezone.utc)
        self.assertIsNone(nz.parse_timestamp("garbage"))
        self.assertIsNone(nz.parse_timestamp(None))

    def test_ist_date_boundary(self):
        late_utc = nz.parse_timestamp("2026-10-05T20:00:00Z")  # 01:30 next day IST
        self.assertEqual(late_utc.astimezone(nz.IST).date().isoformat(), "2026-10-06")


class DerivationTests(unittest.TestCase):
    def test_override_beats_later_set(self):
        sets = [(100, 1, "callback"), (300, 3, "not_interested")]
        overs = [(200, 2, "renewed")]
        self.assertEqual(derive.effective_disposition(sets, overs), "renewed")

    def test_latest_override_wins_and_ties_break_by_arrival(self):
        overs = [(200, 5, "a"), (200, 7, "b"), (100, 9, "c")]
        self.assertEqual(derive.effective_disposition([], overs), "b")

    def test_latest_set_without_override_and_none(self):
        self.assertEqual(derive.effective_disposition([(1, 1, "x"), (2, 2, "y")], []), "y")
        self.assertIsNone(derive.effective_disposition([], []))

    def test_bucket_precedence(self):
        term = lambda c: c == "renewed"
        mk = lambda cid, eff=None, fail=False: CallFacts(cid, 1, False, fail, eff)
        self.assertEqual(derive.lead_bucket([], term), "yet_to_reach_out")
        # completed beats everything, even when every call also failed telephony
        self.assertEqual(derive.lead_bucket([mk("a", "renewed", True)], term), "completed")
        self.assertEqual(derive.lead_bucket([mk("a", None, True), mk("b", None, True)], term),
                         "in_progress_telephony_failed")
        self.assertEqual(derive.lead_bucket([mk("a", None, True), mk("b", None, False)], term),
                         "in_progress_dialed")
        self.assertEqual(derive.lead_bucket([mk("a", "callback")], term), "in_progress_dialed")

    def test_current_disposition_is_latest_call_that_has_one(self):
        calls = [CallFacts("a", 100, True, False, "callback"),
                 CallFacts("b", 200, True, False, None)]
        self.assertEqual(derive.current_disposition(calls), "callback")
        self.assertIsNone(derive.current_disposition([CallFacts("a", 1, False, False, None)]))


if __name__ == "__main__":
    unittest.main()


class SchemaLoaderTests(unittest.TestCase):
    def test_accepts_list_or_mapping_shapes_and_flags(self):
        import json, tempfile
        from pathlib import Path
        from ledger.schema import load_schema
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "wf_a.json"
            a.write_text(json.dumps({"workflow_id": "wf_a", "fields": [
                {"name": "ph", "type": "phone", "required": True, "pii": True},
                {"name": "ref", "type": "string", "reference": True}]}))
            b = Path(d) / "wf_b.json"
            b.write_text(json.dumps({"columns": {
                "ph": {"type": "phone", "required": "true"},
                "lang": {"type": "enum", "values": ["en", "hi"], "default": "en"}}}))
            sa, sb = load_schema(a), load_schema(b)
        self.assertEqual(sa.phone_field.name, "ph")
        self.assertEqual(sa.reference_field.name, "ref")
        self.assertTrue(sa.fields[0].pii)
        self.assertEqual(sb.workflow_id, "wf_b")            # falls back to the file stem
        self.assertTrue(sb.fields[0].required)
        self.assertEqual(sb.fields[1].default, "en")
