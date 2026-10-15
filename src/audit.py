"""Append-only audit trail (JSONL). One entry per hire *whenever its report changes*.
Idempotent: re-running on unchanged data appends nothing."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path


def report_digest(r) -> str:
    return hashlib.sha256(json.dumps(r.to_dict(), sort_keys=True).encode()).hexdigest()[:12]


def record_run(reports, as_of: str, path, now: datetime | None = None) -> int:
    p, last = Path(path), {}
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                e = json.loads(line)
                last[e["person_id"]] = e["report_digest"]
            except (ValueError, KeyError):
                continue                     # tolerate a corrupt line; never rewrite history
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    new = [{"ts": ts, "as_of": as_of, "person_id": r.person_id, "status": r.status,
            "errors": r.errors, "warnings": r.warnings, "issue_ids": [i.issue_id for i in r.issues],
            "report_digest": report_digest(r), "actor": "onboarding-validator"}
           for r in reports if last.get(r.person_id) != report_digest(r)]
    if new:
        with p.open("a") as f:
            for e in new:
                f.write(json.dumps(e, sort_keys=True) + "\n")
    return len(new)
