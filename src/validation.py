"""Deterministic validation rules. No I/O except via injected connectors, no randomness,
no hidden clock (as_of is an input) -> same input always yields byte-identical output."""
from __future__ import annotations
import time
from datetime import date, datetime
from typing import Callable

from .common import OWNER, blank, make_issue
from .models import *  # noqa: F401,F403
from .monitoring import check_gates
from .mock_connectors import ConnectorError, MalformedRecordError

REQUIRED_SOURCE_FIELDS = ["name", "start_date", "work_location", "work_email", "manager"]

# Which source fields each downstream system must agree on.
COMPARISONS = {
    "Workday":        ["name", "start_date", "work_location", "work_email", "manager"],
    "Okta":           ["name", "work_email"],
    "ExpoIT":         ["name", "start_date", "work_location"],
    "Cohort Tracker": ["name", "start_date", "work_location"],
}

# Severity policy. Default is error; softer fields / systems are warnings.
WARNING_FIELDS = {("Workday", "manager"), ("Workday", "name"), ("Okta", "name"), ("ExpoIT", "name")}
WARNING_SYSTEMS = {"Cohort Tracker"}          # tracker drift doesn't block day-one access
WARNING_MISSING_RECORD = {"Cohort Tracker"}

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%B %d, %Y")  # NB: slash dates assumed US
LOCATION_ALIASES = {"sf": "san francisco", "sfo": "san francisco", "nyc": "new york",
                    "new york city": "new york", "ldn": "london"}
OKTA_OK_STATUS = {"ACTIVE", "STAGED", "PROVISIONED"}


# ---------- normalization ----------
def norm_date(v) -> str:
    s = str(v).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unrecognised date '{v}'")


def norm_text(v) -> str:
    return " ".join(str(v).split()).casefold()


def norm_location(v) -> str:
    base = norm_text(v).split(",")[0].strip()          # "San Francisco, CA" -> "san francisco"
    return LOCATION_ALIASES.get(base, base)


NORMALIZERS = {"start_date": norm_date, "work_location": norm_location,
               "work_email": norm_text, "name": norm_text, "manager": norm_text}


def normalize(field_name: str, value) -> str:
    return NORMALIZERS.get(field_name, norm_text)(value)


def _severity(system: str, field_name: str) -> str:
    return WARNING if (system in WARNING_SYSTEMS or (system, field_name) in WARNING_FIELDS) else ERROR


# ---------- rules ----------
def check_source(ashby: dict):
    """Required-field + format checks on the source of truth. Returns (issues, normalized values)."""
    pid, issues, norm = ashby["candidate_id"], [], {}
    for f in REQUIRED_SOURCE_FIELDS:
        v = ashby.get(f)
        if blank(v):
            issues.append(make_issue(pid, "Ashby", f, MISSING_REQUIRED, ERROR, "non-empty value", v,
                                     f"Required field '{f}' is empty in the source record.",
                                     f"{OWNER['Ashby']}: complete '{f}' in Ashby before downstream provisioning."))
            continue
        try:
            norm[f] = normalize(f, v)
        except ValueError as e:
            issues.append(make_issue(pid, "Ashby", f, INVALID_VALUE, ERROR, "valid value", v, str(e),
                                     f"{OWNER['Ashby']}: correct '{f}' in Ashby."))
    return issues, norm


def check_record(pid: str, system: str, ashby_norm: dict, rec: dict):
    issues = []
    for f in COMPARISONS[system]:
        if f not in ashby_norm:           # source value missing/invalid: already reported, can't compare
            continue
        sev, observed = _severity(system, f), rec.get(f)
        if blank(observed):
            issues.append(make_issue(pid, system, f, MISSING_VALUE, sev, ashby_norm[f], observed,
                                     f"{system} record has no '{f}'.",
                                     f"{OWNER[system]}: populate '{f}' in {system}."))
            continue
        try:
            obs_norm = normalize(f, observed)
        except ValueError as e:
            issues.append(make_issue(pid, system, f, INVALID_VALUE, sev, ashby_norm[f], observed, str(e),
                                     f"{OWNER[system]}: fix the '{f}' format in {system}."))
            continue
        if obs_norm != ashby_norm[f]:
            issues.append(make_issue(pid, system, f, MISMATCH, sev, ashby_norm[f], obs_norm,
                                     f"'{f}' differs: Ashby={ashby_norm[f]!r} vs {system}={obs_norm!r}.",
                                     f"{OWNER[system]}: review '{f}' in {system} against Ashby before it is used downstream."))
    return issues


def check_cross_rules(pid: str, system: str, ashby_norm: dict, rec: dict):
    issues = []
    if system == "Okta" and rec.get("status") and rec["status"].upper() not in OKTA_OK_STATUS:
        issues.append(make_issue(pid, system, "status", INVALID_VALUE, WARNING, sorted(OKTA_OK_STATUS), rec["status"],
                                 f"Okta account status '{rec['status']}' is not provisionable.",
                                 f"{OWNER[system]}: check why the account is {rec['status']}."))
    if system == "ExpoIT" and "start_date" in ashby_norm and rec.get("ship_by"):
        try:
            if norm_date(rec["ship_by"]) > ashby_norm["start_date"]:
                issues.append(make_issue(pid, system, "ship_by", SEQUENCE, WARNING, f"<= {ashby_norm['start_date']}",
                                         norm_date(rec["ship_by"]),
                                         "Equipment is scheduled to ship after the start date.",
                                         f"{OWNER[system]}: expedite shipping or confirm day-one workaround."))
        except ValueError:
            pass
    return issues


def _fetch_with_retry(fetch, pid, attempts: int, backoff: float):
    """Retry transient ConnectorError only; malformed data is not retried (it will not fix itself)."""
    for n in range(attempts):
        try:
            return fetch(pid)
        except ConnectorError as e:
            if n == attempts - 1:
                raise ConnectorError(f"{e} (after {attempts} attempts)") from e
            time.sleep(backoff * (2 ** n))          # exponential backoff; 0 in the prototype


def validate_hire(ashby: dict, connectors: dict[str, Callable], as_of: date | None = None,
                  retries: int = 3, backoff: float = 0.0) -> HireReport:
    pid = ashby["candidate_id"]
    issues, ashby_norm = check_source(ashby)
    records: dict[str, dict] = {}

    for system, fetch in connectors.items():
        try:
            rec = _fetch_with_retry(fetch, pid, retries, backoff)
        except ConnectorError as e:
            issues.append(make_issue(pid, system, "*", CONNECTOR_ERROR, ERROR, "reachable system", None, str(e),
                                     f"{OWNER[system]}: retry later / check {system} status; this hire cannot be verified yet."))
            continue
        except MalformedRecordError as e:
            issues.append(make_issue(pid, system, "*", MALFORMED, ERROR, "schema-valid record", None, str(e),
                                     f"{OWNER[system]}: inspect the {system} record; the integration returned bad data."))
            continue
        if rec is None:
            sev = WARNING if system in WARNING_MISSING_RECORD else ERROR
            issues.append(make_issue(pid, system, "*", MISSING_RECORD, sev, "a matching record", None,
                                     f"No {system} record found for {pid}.",
                                     f"{OWNER[system]}: create/link the {system} record for {pid}."))
            continue
        records[system] = rec
        issues += check_record(pid, system, ashby_norm, rec)
        issues += check_cross_rules(pid, system, ashby_norm, rec)

    if as_of is not None:
        issues += check_gates(pid, ashby_norm, records, as_of)

    issues.sort(key=lambda i: (i.severity != ERROR, i.system, i.field, i.issue_type))  # deterministic order
    return HireReport(pid, ashby.get("name", "?"), compute_status(issues), issues)
