"""Deterministic rendering. (Claude triage lives in triage.py.)"""
from __future__ import annotations
import hashlib, json
from .models import HireReport

ICON = {"PASS": "[ PASS    ]", "REVIEW": "[ REVIEW  ]", "BLOCKED": "[ BLOCKED ]"}


def to_json(reports: list[HireReport]) -> str:
    return json.dumps([r.to_dict() for r in reports], indent=2, sort_keys=True)


def digest(reports: list[HireReport]) -> str:
    return hashlib.sha256(to_json(reports).encode()).hexdigest()[:12]


def render_text(reports: list[HireReport], as_of: str = "") -> str:
    out = [f"Onboarding validation  (as_of={as_of})" if as_of else "Onboarding validation"]
    for r in reports:
        out.append(f"\n{ICON[r.status]} {r.person_id}  {r.name}   errors={r.errors} warnings={r.warnings}")
        for i in r.issues:
            out.append(f"   - {i.severity.upper():7} {i.system}.{i.field} ({i.issue_type}) id={i.issue_id}")
            out.append(f"       {i.message}")
            out.append(f"       -> {i.suggested_action}")
    counts = {s: sum(r.status == s for r in reports) for s in ("PASS", "REVIEW", "BLOCKED")}
    out.append(f"\nSummary: {counts}   report digest: {digest(reports)}")
    return "\n".join(out)
