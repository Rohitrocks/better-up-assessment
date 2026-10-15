"""Optional MCP server exposing the validator as read-only tools for Claude / Claude Code.
   pip install mcp ;  python -m src.mcp_server   (stdio transport)"""
from mcp.server.fastmcp import FastMCP
from .main import run

mcp = FastMCP("onboarding-validator")


@mcp.tool()
def validate_hire(person_id: str, as_of: str = "") -> dict:
    """Validate one hire's records across Ashby, Workday, Okta, ExpoIT and the cohort tracker, including
    deadline gates. Read-only and idempotent. Returns status (PASS/REVIEW/BLOCKED) and issues."""
    reports = run([person_id], as_of or None)
    return reports[0].to_dict() if reports else {"error": f"unknown hire {person_id}"}


@mcp.tool()
def list_open_exceptions(as_of: str = "") -> list[dict]:
    """List every hire (and orphan record) that is not PASS, with error/warning counts. Read-only."""
    return [{"person_id": r.person_id, "name": r.name, "status": r.status, "errors": r.errors,
             "warnings": r.warnings} for r in run(None, as_of or None) if r.status != "PASS"]


if __name__ == "__main__":
    mcp.run()
