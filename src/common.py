"""Shared helpers: ownership routing and the stable issue factory."""
from __future__ import annotations
import hashlib
from .models import Issue

# Which team owns each system (used for routing / Slack drafts).
OWNER = {"Workday": "People Ops / HRIS", "Okta": "IT / Identity", "ExpoIT": "IT / Procurement",
         "Cohort Tracker": "Recruiting Coordination", "Ashby": "Recruiting"}


def blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def make_issue(person_id, system, field_name, itype, severity, expected, observed, message, action) -> Issue:
    key = f"{person_id}|{system}|{field_name}|{itype}"      # stable identity -> idempotent reports
    return Issue(hashlib.sha1(key.encode()).hexdigest()[:10], person_id, system, field_name,
                 itype, severity, expected, observed, message, action)
