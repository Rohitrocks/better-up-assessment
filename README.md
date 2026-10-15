# Onboarding Cross-System Validator + Exception Monitor (prototype)

Read-only engine that reconciles a new hire's **Ashby** record (source of truth) against **Workday, Okta/Lumos,
ExpoIT and the cohort tracker**, watches the **7-day process gates**, finds **orphan records** (off-cycle hires
created outside Ashby), and uses **Claude** to triage the detected exceptions.

Docs: [`docs/SYSTEMS_MAP.md`](docs/SYSTEMS_MAP.md) (systems map, why this slice, existing-stack constraint) ·
[`docs/BUILD_NOTE.md`](docs/BUILD_NOTE.md) (design decisions, productionizing, AI mistakes).

## Run (from the project root)
```bash
python3 -m src.main                         # all hires + orphan scan (as_of 2026-10-30)
python3 -m src.main --hire C-1042           # one hire
python3 -m src.main --as-of today           # use the real date
python3 -m src.main --repeat                # prove identical output on a re-run
python3 -m src.main --audit                 # append changes to data/audit_log.jsonl
python3 -m src.main --triage                # Claude triage (rules fallback if no ANTHROPIC_API_KEY)
python3 -m unittest discover -s tests -t .  # 18 tests
```
Optional: `pip install -r requirements.txt` for `anthropic` (real Claude), `mcp<2`
(`python -m src.mcp_server`) and `fastapi uvicorn` (`uvicorn src.api:app`, `POST /validate`).

## Demo cases (fictional data, as_of 2026-10-30)
| ID | Scenario | Result |
|---|---|---|
| C-1001 | Everything agrees (despite `11/02/2026`, `SF`, `San Francisco, CA`) | PASS |
| C-1042 | Location mismatch in Workday + ExpoIT, manager drift, ships late, **hardware form overdue** | BLOCKED |
| C-1077 | Ashby email empty, no Okta record, no cohort record | BLOCKED |
| C-1099 | Okta timeout (retried), ExpoIT malformed data | BLOCKED |
| C-1120 | Cohort tracker start-date drift, **background check due in 3 days** | REVIEW |
| C-9001 | In Workday/Okta/ExpoIT but **not in Ashby** (off-cycle hire) | REVIEW |

## Layout
```
src/models.py          Issue, HireReport, status
src/common.py          ownership routing, stable issue factory
src/mock_connectors.py mocked Ashby/Workday/Okta/ExpoIT/cohort clients (+ failure simulation)
src/validation.py      normalization, field rules, severity policy, retries
src/monitoring.py      deadline gates, orphan detection
src/triage.py          Claude tool-use triage + validation guardrails + rules fallback
src/audit.py           append-only, idempotent JSONL audit log
src/report.py          text / JSON output, digest
src/main.py            CLI      src/api.py  HTTP wrapper      src/mcp_server.py  MCP tools
mcp/tool_definition.json   MCP tool definitions
```
