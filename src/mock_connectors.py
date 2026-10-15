"""Mock connectors. Each get_* mimics a real API client:
    returns dict            -> record found
    returns None            -> no record for this person (404-equivalent)
    raises ConnectorError   -> system unavailable / timeout
    raises MalformedRecordError -> response fails schema validation
To productionize, replace each body with an authenticated HTTP call behind the same contract;
validation.py / monitoring.py would not change. Read-only by design: nothing here writes anywhere.
"""
from __future__ import annotations
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class ConnectorError(Exception):
    """Transient/unavailable system (timeout, 5xx)."""


class MalformedRecordError(Exception):
    """Record returned but violates the expected schema."""


# canonical field -> accepted python type, per system (stand-in for a response schema)
SCHEMAS = {
    "workday": {"name": str, "start_date": str, "work_location": str, "work_email": str,
                "manager": str, "background_check": str},
    "okta":    {"name": str, "work_email": str, "status": str},
    "expoit":  {"name": str, "work_location": str, "start_date": str, "ship_by": str, "form_submitted": bool},
    "cohort":  {"name": str, "start_date": str, "work_location": str},
}


def _load(name: str):
    return json.loads((DATA_DIR / name).read_text())


def _ids(system: str) -> list[str]:
    return sorted(_load("systems.json").get(system, {}))


def _fetch(system: str, person_id: str):
    record = _load("systems.json").get(system, {}).get(person_id)
    if record is None:
        return None
    if record.get("__simulate") == "timeout":
        raise ConnectorError(f"{system}: request timed out after 10s (simulated)")
    for key, typ in SCHEMAS[system].items():
        if key in record and record[key] is not None and not isinstance(record[key], typ):
            raise MalformedRecordError(
                f"{system}: field '{key}' expected {typ.__name__}, got {type(record[key]).__name__}")
    return record


def list_hire_ids() -> list[str]:
    return [h["candidate_id"] for h in _load("hires.json")]


def get_ashby_hire(person_id: str):
    for h in _load("hires.json"):
        if h["candidate_id"] == person_id:
            return h
    return None


def get_workday_employee(person_id: str):   return _fetch("workday", person_id)
def get_okta_user(person_id: str):          return _fetch("okta", person_id)
def get_expoit_request(person_id: str):     return _fetch("expoit", person_id)
def get_cohort_record(person_id: str):      return _fetch("cohort", person_id)


CONNECTORS = {
    "Workday": get_workday_employee,
    "Okta": get_okta_user,
    "ExpoIT": get_expoit_request,
    "Cohort Tracker": get_cohort_record,
}

LISTERS = {   # "list ids" endpoints, used for orphan detection
    "Workday": lambda: _ids("workday"),
    "Okta": lambda: _ids("okta"),
    "ExpoIT": lambda: _ids("expoit"),
    "Cohort Tracker": lambda: _ids("cohort"),
}
