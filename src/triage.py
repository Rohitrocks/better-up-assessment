"""Claude as an exception handler, with guardrails.

Division of labour:
  * deterministic code  -> finds issues, decides PASS/REVIEW/BLOCKED (authoritative, never touched here)
  * Claude (tool use)   -> triages ALREADY-DETECTED issues: likely root cause, urgency, Slack draft
  * validate_triage()   -> treats Claude's output as untrusted: schema/enum checks, unknown ids dropped,
                           urgency floor for errors, gaps filled by the rules-based fallback
Claude never marks anything valid, never changes data, and never posts to Slack (drafts only).
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field

ROOT_CAUSES = ("source_ashby", "downstream_system", "integration_failure", "process_delay", "unclear")
URGENCY = ("now", "today", "this_week")
_URGENCY_RANK = {u: i for i, u in enumerate(URGENCY)}

TOOL = {
    "name": "record_triage",
    "description": "Record triage for detected onboarding validation issues. Use only the provided issue_ids.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "issue_id": {"type": "string"},
                "likely_root_cause": {"type": "string", "enum": list(ROOT_CAUSES)},
                "urgency": {"type": "string", "enum": list(URGENCY)},
                "rationale": {"type": "string", "description": "One sentence, max 240 chars."}},
                "required": ["issue_id", "likely_root_cause", "urgency", "rationale"]}},
            "slack_drafts": {"type": "array", "items": {"type": "object", "properties": {
                "person_id": {"type": "string"},
                "message": {"type": "string", "description": "Short Slack message to the owning team(s)."}},
                "required": ["person_id", "message"]}},
        },
        "required": ["items", "slack_drafts"],
    },
}

SYSTEM_PROMPT = (
    "You are an exception-triage assistant for People Technology onboarding. You receive issues that "
    "deterministic code has ALREADY detected; they are authoritative. Do not add, remove or re-judge "
    "issues, never declare a record valid, and only reference the given issue_ids. For each issue give the "
    "most likely root cause (is the source Ashby wrong, is a downstream system stale, did an integration "
    "fail, or is a process step late?) and an urgency, using the start dates and as_of date for context. "
    "Then draft one concise Slack message per person that lists what each owning team must do. "
    "Respond only by calling record_triage."
)


@dataclass
class Triage:
    items: list = field(default_factory=list)
    slack_drafts: list = field(default_factory=list)
    rejected: int = 0           # Claude outputs dropped by validation


def _issues(reports):
    return [i for r in reports for i in r.issues]


# ---------- deterministic fallback ----------
_CAUSE = {"missing_required": "source_ashby", "invalid_value": "unclear", "mismatch": "unclear",
          "missing_value": "downstream_system", "missing_record": "process_delay",
          "connector_error": "integration_failure", "malformed_record": "integration_failure",
          "sequence_violation": "process_delay", "gate_overdue": "process_delay",
          "gate_due_soon": "process_delay", "orphan_record": "unclear"}


def _fallback_urgency(i) -> str:
    if i.issue_type == "gate_overdue":
        return "now"
    return "today" if i.severity == "error" else "this_week"


def _fallback_draft(r) -> dict:
    teams = sorted({i.suggested_action.split(":")[0] for i in r.issues})
    top = r.issues[0]
    return {"person_id": r.person_id, "source": "rules",
            "message": (f"{r.person_id} ({r.name}) is {r.status}: {r.errors} error(s), {r.warnings} warning(s). "
                        f"Owners: {', '.join(teams)}. First action: {top.suggested_action}")}


def fallback_triage(reports) -> Triage:
    items = [{"issue_id": i.issue_id, "likely_root_cause": _CAUSE.get(i.issue_type, "unclear"),
              "urgency": _fallback_urgency(i), "rationale": i.message[:240], "source": "rules"}
             for i in _issues(reports)]
    drafts = [_fallback_draft(r) for r in reports if r.issues]
    return Triage(items, drafts)


# ---------- treat model output as untrusted ----------
def validate_triage(raw, reports) -> Triage:
    issues = {i.issue_id: i for i in _issues(reports)}
    people = {r.person_id for r in reports if r.issues}
    out, rejected = {}, 0
    raw = raw if isinstance(raw, dict) else {}
    for it in raw.get("items") or []:
        ok = (isinstance(it, dict) and it.get("issue_id") in issues
              and it.get("likely_root_cause") in ROOT_CAUSES and it.get("urgency") in URGENCY)
        if not ok:
            rejected += 1
            continue
        urgency = it["urgency"]
        if issues[it["issue_id"]].severity == "error" and _URGENCY_RANK[urgency] > _URGENCY_RANK["today"]:
            urgency = "today"        # rules outrank the model: an error is never "this_week"
        out[it["issue_id"]] = {"issue_id": it["issue_id"], "likely_root_cause": it["likely_root_cause"],
                               "urgency": urgency, "rationale": str(it.get("rationale", ""))[:240],
                               "source": "claude"}
    drafts = {}
    for d in raw.get("slack_drafts") or []:
        if isinstance(d, dict) and d.get("person_id") in people and isinstance(d.get("message"), str) and d["message"].strip():
            drafts[d["person_id"]] = {"person_id": d["person_id"], "message": d["message"][:1200], "source": "claude"}
        else:
            rejected += 1
    fb = fallback_triage(reports)
    for f in fb.items:                       # every issue always gets a triage entry
        out.setdefault(f["issue_id"], f)
    for f in fb.slack_drafts:
        drafts.setdefault(f["person_id"], f)
    order = {i.issue_id: n for n, i in enumerate(_issues(reports))}
    return Triage(sorted(out.values(), key=lambda x: order[x["issue_id"]]),
                  [drafts[p] for p in sorted(drafts)], rejected)


def claude_triage(reports, as_of: str = "", client=None, model: str | None = None):
    """Returns (Triage, source). Any failure -> rules-based fallback; the demo never depends on the API."""
    issues = _issues(reports)
    if not issues:
        return Triage(), "rules (no issues)"
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return fallback_triage(reports), "rules (no ANTHROPIC_API_KEY)"
        try:
            import anthropic
            client = anthropic.Anthropic(timeout=20.0, max_retries=2)
        except Exception as e:
            return fallback_triage(reports), f"rules ({type(e).__name__})"
    payload = {"as_of": as_of,
               "people": {r.person_id: {"name": r.name, "status": r.status} for r in reports if r.issues},
               "issues": [{k: v for k, v in i.to_dict().items() if k != "suggested_action"} for i in issues]}
    try:
        msg = client.messages.create(
            model=model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=2000,
            system=SYSTEM_PROMPT, tools=[TOOL], tool_choice={"type": "tool", "name": TOOL["name"]},
            messages=[{"role": "user", "content": json.dumps(payload, indent=1)}])
        block = next(b for b in msg.content if getattr(b, "type", "") == "tool_use")
        return validate_triage(block.input, reports), "claude"
    except Exception as e:     # network, auth, rate limit, no tool_use block, bad schema ...
        return fallback_triage(reports), f"rules ({type(e).__name__})"


def render_triage(tri: Triage, source: str) -> str:
    lines = [f"\n--- Exception triage [{source}]" + (f", {tri.rejected} model output(s) rejected" if tri.rejected else "") + " ---"]
    for it in tri.items:
        lines.append(f"  {it['issue_id']}  {it['urgency']:9} {it['likely_root_cause']:19} ({it['source']}) {it['rationale']}")
    lines.append("\nSlack drafts (NOT sent; a human or the n8n approval step posts them):")
    for d in tri.slack_drafts:
        lines.append(f"  [{d['source']}] {d['message']}")
    return "\n".join(lines)
