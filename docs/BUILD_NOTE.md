# Build note

## Why this slice
See `SYSTEMS_MAP.md`: validation + monitoring is read-only, immediately safe, and is the check that
propagation and alerting both depend on.

## Key design decisions
- **Data model**: every output is an `Issue(issue_id, person_id, system, field, issue_type, severity, expected,
  observed, message, suggested_action)`. A hire's status is derived from its issues
  (any error -> BLOCKED, only warnings -> REVIEW, none -> PASS). Everything else (text, JSON, audit,
  triage, Slack drafts) is a view of issues.
- **Connectors vs rules**: `mock_connectors.py` only fetches (found / `None` / `ConnectorError` /
  `MalformedRecordError`); `validation.py` and `monitoring.py` only judge. Swapping a mock for a real
  authenticated client changes one function.
- **Normalize before comparing**: dates in several formats, casing/whitespace, location aliases (`SF`,
  `San Francisco, CA`). This avoids false alarms that would make people ignore the tool.
- **Severity policy is data**: manager/name drift and cohort-tracker drift are warnings; location, date, email,
  missing records and overdue gates are errors.
- **Failure handling**: timeouts are retried with exponential backoff (0s in the prototype), then reported as a
  `connector_error` for that system only; malformed data is reported, not retried; a missing record is its own
  issue. One bad system never stops the others, and "cannot verify" is BLOCKED, not PASS.
- **Idempotency**: read-only; issue IDs are hashes of (person, system, field, type); output is sorted and has no
  timestamps; the evaluation date `as_of` is an explicit input, so the same inputs give a byte-identical report
  (`--repeat` shows the digest). The audit log appends only when a hire's report digest changes, so reruns
  create no duplicates.
- **Claude as a bounded exception handler** (`triage.py`): code finds issues and decides status. Claude receives
  only detected issues, is forced to answer through a tool schema, and returns root-cause hypothesis
  (Ashby wrong vs downstream stale vs integration vs process), urgency and a Slack draft per hire. Its output is
  treated as untrusted: unknown issue IDs and invalid enums are dropped, an error can never be downgraded to
  `this_week`, gaps are filled by the rules fallback, and drafts are never posted automatically. Any API
  failure falls back to the rules, so the demo works offline.

## Bonus items
- **MCP**: `src/mcp_server.py` (+ `mcp/tool_definition.json`) exposes `validate_hire` and
  `list_open_exceptions` as read-only, idempotent tools.
- **Audit trail**: `--audit` writes `data/audit_log.jsonl` (who/what/when, issue IDs, digest).
- **Auth approach (design only)**: per-system service accounts using OAuth2 client credentials with read-only
  scopes; secrets in the company secret store, not env files; MCP server behind SSO (Okta OIDC) with per-tool
  authorization; PII-minimal logs (IDs, not names/emails); Claude receives issue fields only.

## What I'd productionize
Real connectors (pagination, rate limits, schema contracts per vendor), n8n schedule + webhook on Ashby changes,
Slack posting through an approval step, a store for audit history and issue state (open / acknowledged /
resolved, dedupe alerts by `issue_id`), SLO alerting on the validator itself, a property-based test suite for
normalizers, then a *propagation* slice that reuses the same issue model.

## Tools used
Claude (design, code generation, review), Claude Code/Cursor-style workflow for iterating and running tests.
(Edit this to match what you actually used.)

## Things the AI got wrong, and how I caught them
1. **Shell scaffolding**: the first scaffold command used brace expansion (`mkdir -p src,data,tests`) under a
   shell that doesn't expand it, creating a literal folder named `{src,data,tests}`; every file write then
   failed. Caught because the run immediately errored (`No module named 'src'`) and test discovery failed.
   Fix: explicit `mkdir` calls, and always run the tests in the same step as scaffolding.
2. **Outdated library API**: the generated MCP server used `FastMCP` from the MCP SDK; the latest SDK renamed
   it, so the import failed. Caught by actually importing the module and listing the tools in a clean
   virtualenv instead of trusting the code. Fix: pin `mcp<2` and document it.
(Replace or confirm these with your own experience before you present; you must be able to defend them.)
