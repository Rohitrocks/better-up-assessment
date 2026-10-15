"""Exception monitoring: process gates with deadlines, and orphan records (off-cycle hires).
Pure functions; `as_of` is an explicit input so results are reproducible (no hidden clock)."""
from __future__ import annotations
import sys
from datetime import date, timedelta
from typing import Callable

from .common import OWNER, make_issue
from .models import (ERROR, WARNING, GATE_OVERDUE, GATE_DUE_SOON, ORPHAN, HireReport, compute_status)
from .mock_connectors import ConnectorError

GATE_LEAD_DAYS = 7      # each gate is due 7 days before the start date
DUE_SOON_DAYS = 7       # warn when the deadline is within 7 days

# gate -> (owning system, is_done(record), describe_state(record))
GATES = {
    "hardware_form":     ("ExpoIT",   lambda r: r.get("form_submitted") is True,
                          lambda r: "submitted" if r.get("form_submitted") is True else "not submitted"),
    "background_check":  ("Workday",  lambda r: str(r.get("background_check", "")).lower() == "cleared",
                          lambda r: str(r.get("background_check") or "unknown")),
    "okta_provisioning": ("Okta",     lambda r: str(r.get("status", "")).upper() in {"ACTIVE", "PROVISIONED"},
                          lambda r: str(r.get("status") or "unknown")),
}


def check_gates(pid: str, ashby_norm: dict, records: dict, as_of: date):
    if "start_date" not in ashby_norm:
        return []
    start = date.fromisoformat(ashby_norm["start_date"])
    due = start - timedelta(days=GATE_LEAD_DAYS)
    days_left = (due - as_of).days
    issues = []
    for gate, (system, done, state) in GATES.items():
        rec = records.get(system)
        if rec is None or done(rec):        # no record -> already reported as missing_record / connector error
            continue
        owner = OWNER[system]
        if days_left < 0:
            issues.append(make_issue(pid, system, gate, GATE_OVERDUE, ERROR, f"complete by {due}", state(rec),
                                     f"Gate '{gate}' is {-days_left} day(s) overdue (due {due}, start {start}).",
                                     f"{owner}: complete '{gate}' now or escalate; day-one readiness at risk."))
        elif days_left <= DUE_SOON_DAYS:
            issues.append(make_issue(pid, system, gate, GATE_DUE_SOON, WARNING, f"complete by {due}", state(rec),
                                     f"Gate '{gate}' not complete; due in {days_left} day(s) ({due}).",
                                     f"{owner}: complete '{gate}' before {due}."))
    return issues


def find_orphans(ashby_ids: set, listers: dict[str, Callable[[], list]], fetchers: dict[str, Callable]):
    """Records present downstream but absent from Ashby, e.g. an off-cycle hire created directly in Workday."""
    found: dict[str, dict[str, str]] = {}
    for system, lister in listers.items():
        try:
            ids = lister()
        except ConnectorError as e:
            print(f"[warn] orphan scan skipped for {system}: {e}", file=sys.stderr)
            continue
        for pid in ids:
            if pid in ashby_ids:
                continue
            name = "?"
            try:
                name = (fetchers[system](pid) or {}).get("name", "?")
            except Exception:
                pass
            found.setdefault(pid, {})[system] = name
    reports = []
    for pid in sorted(found):
        issues = [make_issue(pid, s, "*", ORPHAN, WARNING, "an Ashby record", None,
                             f"{s} has a record for {pid} but Ashby does not (off-cycle hire?).",
                             f"{OWNER['Ashby']}: confirm this off-cycle hire and create/link the Ashby record, "
                             f"or ask {OWNER[s]} to remove it.") for s in sorted(found[pid])]
        name = next((n for n in found[pid].values() if n != "?"), "?")
        reports.append(HireReport(pid, name, compute_status(issues), issues))
    return reports
