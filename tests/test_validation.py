import json, os, tempfile, unittest
from datetime import date
from pathlib import Path
from unittest import mock

from src import mock_connectors as mc
from src.audit import record_run
from src.main import run
from src.monitoring import check_gates
from src.report import to_json
from src.triage import claude_triage, fallback_triage, validate_triage
from src.validation import norm_date, norm_location, validate_hire


def one(pid):
    return run([pid])[0]


def by_id(reports, pid):
    return next(r for r in reports if r.person_id == pid)


class Core(unittest.TestCase):
    def test_clean_passes_despite_formatting_noise(self):
        r = one("C-1001")
        self.assertEqual((r.status, r.issues), ("PASS", []))

    def test_mismatch_blocked(self):
        r = one("C-1042")
        self.assertEqual(r.status, "BLOCKED")
        fields = {(i.system, i.field, i.severity) for i in r.issues}
        self.assertIn(("Workday", "work_location", "error"), fields)
        self.assertIn(("Workday", "manager", "warning"), fields)
        self.assertIn(("ExpoIT", "ship_by", "warning"), fields)

    def test_missing_data(self):
        types = {(i.system, i.issue_type) for i in one("C-1077").issues}
        self.assertIn(("Ashby", "missing_required"), types)
        self.assertIn(("Okta", "missing_record"), types)
        self.assertIn(("Cohort Tracker", "missing_record"), types)

    def test_connector_failures_are_contained(self):
        r = one("C-1099")
        types = {(i.system, i.issue_type) for i in r.issues}
        self.assertIn(("Okta", "connector_error"), types)
        self.assertIn(("ExpoIT", "malformed_record"), types)
        self.assertEqual(r.status, "BLOCKED")

    def test_idempotent_and_stable_ids(self):
        a, b = run(), run()
        self.assertEqual(to_json(a), to_json(b))
        ids = [i.issue_id for r in a for i in r.issues]
        self.assertEqual(len(ids), len(set(ids)))

    def test_normalizers(self):
        self.assertEqual(norm_date("11/02/2026"), "2026-11-02")
        self.assertEqual(norm_location("NYC"), "new york")
        with self.assertRaises(ValueError):
            norm_date("2026-13-45")

    def test_garbage_date_downstream_is_issue_not_crash(self):
        ashby = mc.get_ashby_hire("C-1001")
        conns = {"Workday": lambda pid: {"name": "Priya Sharma", "start_date": "someday", "work_location": "SF",
                                        "work_email": "priya.sharma@example.com", "manager": "Daniel Kim"}}
        self.assertEqual([i.issue_type for i in validate_hire(ashby, conns).issues], ["invalid_value"])


class Retry(unittest.TestCase):
    def test_transient_failure_recovers(self):
        calls = {"n": 0}

        def flaky(pid):
            calls["n"] += 1
            if calls["n"] < 3:
                raise mc.ConnectorError("503")
            return {"name": "Priya Sharma", "work_email": "priya.sharma@example.com", "status": "ACTIVE"}
        r = validate_hire(mc.get_ashby_hire("C-1001"), {"Okta": flaky}, retries=3)
        self.assertEqual((calls["n"], r.issues), (3, []))

    def test_persistent_failure_reported_after_retries(self):
        def down(pid):
            raise mc.ConnectorError("503")
        r = validate_hire(mc.get_ashby_hire("C-1001"), {"Okta": down}, retries=3)
        self.assertEqual(r.issues[0].issue_type, "connector_error")
        self.assertIn("after 3 attempts", r.issues[0].message)


class Monitoring(unittest.TestCase):
    def test_gate_overdue(self):
        i = {(x.system, x.field, x.issue_type, x.severity) for x in one("C-1042").issues}
        self.assertIn(("ExpoIT", "hardware_form", "gate_overdue", "error"), i)

    def test_gate_due_soon_is_review(self):
        r = one("C-1120")
        self.assertEqual(r.status, "REVIEW")
        self.assertIn(("Workday", "background_check", "gate_due_soon"), {(x.system, x.field, x.issue_type) for x in r.issues})

    def test_gate_not_due_yet(self):
        ashby_norm = {"start_date": "2026-12-01"}
        recs = {"ExpoIT": {"form_submitted": False}}
        self.assertEqual(check_gates("X", ashby_norm, recs, date(2026, 10, 30)), [])

    def test_orphan_off_cycle_hire(self):
        r = by_id(run(), "C-9001")
        self.assertEqual(r.status, "REVIEW")
        self.assertEqual({(i.system, i.issue_type) for i in r.issues},
                         {("Workday", "orphan_record"), ("Okta", "orphan_record"), ("ExpoIT", "orphan_record")})


class Audit(unittest.TestCase):
    def test_audit_is_idempotent_and_records_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "audit.jsonl"
            reports = run()
            self.assertEqual(record_run(reports, "2026-10-30", path), len(reports))
            self.assertEqual(record_run(reports, "2026-10-30", path), 0)       # rerun -> no duplicates
            changed = run(None, "2026-11-10")                                  # later date -> gates change
            self.assertGreater(record_run(changed, "2026-11-10", path), 0)
            for line in path.read_text().splitlines():
                json.loads(line)


class _Block:
    type = "tool_use"
    def __init__(self, inp): self.input = inp


class FakeClient:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.messages = payload, exc, self
    def create(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return type("Msg", (), {"content": [_Block(self.payload)]})()


class Triage(unittest.TestCase):
    def setUp(self):
        self.reports = run()
        self.digest_before = to_json(self.reports)
        self.err = next(i for i in one("C-1042").issues if i.issue_type == "gate_overdue")

    def test_claude_output_is_validated(self):
        payload = {"items": [
            {"issue_id": self.err.issue_id, "likely_root_cause": "process_delay", "urgency": "this_week", "rationale": "x"},
            {"issue_id": "deadbeef00", "likely_root_cause": "process_delay", "urgency": "now", "rationale": "hallucinated id"},
            {"issue_id": self.err.issue_id, "likely_root_cause": "made_up", "urgency": "now", "rationale": "bad enum"}],
            "slack_drafts": [{"person_id": "C-1042", "message": "Hardware form is overdue."},
                             {"person_id": "NOPE", "message": "ghost"}]}
        client = FakeClient(payload)
        tri, source = claude_triage(self.reports, "2026-10-30", client=client)
        self.assertEqual(source, "claude")
        self.assertEqual(tri.rejected, 3)
        got = {i["issue_id"]: i for i in tri.items}
        self.assertNotIn("deadbeef00", got)
        self.assertEqual(got[self.err.issue_id]["urgency"], "today")            # error clamped from this_week
        self.assertEqual(len(tri.items), sum(len(r.issues) for r in self.reports))   # every issue covered
        self.assertEqual(client.kwargs["tool_choice"]["name"], "record_triage")
        self.assertEqual(to_json(self.reports), self.digest_before)             # statuses untouched

    def test_api_failure_falls_back(self):
        tri, source = claude_triage(self.reports, client=FakeClient(exc=RuntimeError("boom")))
        self.assertEqual(source, "rules (RuntimeError)")
        self.assertTrue(all(i["source"] == "rules" for i in tri.items))

    def test_no_api_key_falls_back(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.assertEqual(claude_triage(self.reports)[1], "rules (no ANTHROPIC_API_KEY)")

    def test_garbage_response_does_not_crash(self):
        self.assertEqual(validate_triage("not a dict", self.reports).items, fallback_triage(self.reports).items)


if __name__ == "__main__":
    unittest.main()
