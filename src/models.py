"""Plain data models. Issues are the unit of output; everything else derives from them."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

ERROR, WARNING = "error", "warning"
PASS, REVIEW, BLOCKED = "PASS", "REVIEW", "BLOCKED"

# issue types
MISSING_REQUIRED = "missing_required"      # source record lacks a required field
INVALID_VALUE = "invalid_value"            # value present but unparseable / not allowed
MISSING_RECORD = "missing_record"          # downstream system has no record for this person
MISSING_VALUE = "missing_value"            # downstream record exists but field is empty
MISMATCH = "mismatch"                      # downstream value disagrees with source
SEQUENCE = "sequence_violation"            # cross-field ordering rule broken
CONNECTOR_ERROR = "connector_error"        # system unreachable / timed out
MALFORMED = "malformed_record"             # system returned data that fails schema checks


@dataclass(frozen=True)
class Issue:
    issue_id: str          # stable: hash(person, system, field, type) -> same input, same id
    person_id: str
    system: str
    field: str
    issue_type: str
    severity: str
    expected: Any
    observed: Any
    message: str
    suggested_action: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HireReport:
    person_id: str
    name: str
    status: str = PASS
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(i.severity == ERROR for i in self.issues)

    @property
    def warnings(self) -> int:
        return sum(i.severity == WARNING for i in self.issues)

    def to_dict(self) -> dict:
        return {
            "person_id": self.person_id, "name": self.name, "status": self.status,
            "errors": self.errors, "warnings": self.warnings,
            "issues": [i.to_dict() for i in self.issues],
        }

GATE_OVERDUE = "gate_overdue"              # process gate not complete and past its deadline
GATE_DUE_SOON = "gate_due_soon"            # process gate not complete, deadline close
ORPHAN = "orphan_record"                   # downstream record with no Ashby record (off-cycle hire?)


def compute_status(issues) -> str:
    if any(i.severity == ERROR for i in issues):
        return BLOCKED
    return REVIEW if issues else PASS
