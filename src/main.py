"""CLI:  python -m src.main [--hire C-1042] [--as-of 2026-10-30|today] [--json] [--triage] [--audit] [--repeat]"""
from __future__ import annotations
import argparse, sys
from datetime import date
from . import mock_connectors as mc
from .audit import record_run
from .monitoring import find_orphans
from .report import render_text, to_json, digest
from .triage import claude_triage, render_triage
from .validation import validate_hire

DEFAULT_AS_OF = "2026-10-30"      # fixed demo date so output is reproducible; use --as-of today in real runs


def run(hire_ids: list[str] | None = None, as_of: str | None = None):
    as_of_d = date.fromisoformat(as_of or DEFAULT_AS_OF)
    ids = hire_ids or mc.list_hire_ids()
    reports = []
    for pid in ids:
        ashby = mc.get_ashby_hire(pid)
        if ashby is None:
            print(f"Unknown hire id: {pid}", file=sys.stderr)
            continue
        reports.append(validate_hire(ashby, mc.CONNECTORS, as_of=as_of_d))
    if not hire_ids:      # orphan scan only makes sense across the full population
        reports += find_orphans(set(mc.list_hire_ids()), mc.LISTERS, mc.CONNECTORS)
    return reports


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Onboarding cross-system validator + exception monitor (read-only)")
    p.add_argument("--hire", action="append", help="candidate id (repeatable); default = all + orphan scan")
    p.add_argument("--as-of", default=None, help=f"evaluation date YYYY-MM-DD or 'today' (default {DEFAULT_AS_OF})")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    p.add_argument("--triage", action="store_true", help="Claude triage of detected issues (falls back to rules)")
    p.add_argument("--audit", nargs="?", const=str(mc.DATA_DIR / "audit_log.jsonl"), help="append changes to a JSONL audit log")
    p.add_argument("--repeat", action="store_true", help="run twice and prove the output is identical")
    a = p.parse_args(argv)

    as_of = date.today().isoformat() if a.as_of == "today" else (a.as_of or DEFAULT_AS_OF)
    reports = run(a.hire, as_of)
    print(to_json(reports) if a.json else render_text(reports, as_of))

    if a.repeat:
        same = digest(run(a.hire, as_of)) == digest(reports)
        print(f"\nRe-run identical: {same}  (digest {digest(reports)})")
    if a.audit:
        n = record_run(reports, as_of, a.audit)
        print(f"\nAudit: {n} new entr{'y' if n == 1 else 'ies'} appended to {a.audit}")
    if a.triage:
        tri, source = claude_triage(reports, as_of)
        print(render_triage(tri, source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
