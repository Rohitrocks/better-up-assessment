"""Optional thin HTTP wrapper so an n8n HTTP Request node (or Zapier webhook) can call the validator.
   uvicorn src.api:app    then    POST /validate {"hire_id": "C-1042", "as_of": "2026-10-30"}"""
from datetime import date
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from . import mock_connectors as mc
from .main import DEFAULT_AS_OF
from .validation import validate_hire

app = FastAPI(title="Onboarding Validator")


class Req(BaseModel):
    hire_id: str
    as_of: str | None = None


@app.post("/validate")
def validate(req: Req):
    ashby = mc.get_ashby_hire(req.hire_id)
    if ashby is None:
        raise HTTPException(404, f"Unknown hire {req.hire_id}")
    return validate_hire(ashby, mc.CONNECTORS, as_of=date.fromisoformat(req.as_of or DEFAULT_AS_OF)).to_dict()
