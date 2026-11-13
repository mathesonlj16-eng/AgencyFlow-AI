"""AgencyFlow AI: local-first creative operations assistant.

Run: uvicorn app:app --host 127.0.0.1 --port 8766
Requires Python 3.11+. Do not expose publicly without DEMO_ACCESS_CODE.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_client import ModelError, generate_plan
from plan import Plan

BASE = Path(__file__).resolve().parent
DB = Path(os.getenv("DB_PATH", str(BASE / "data" / "agencyflow.sqlite3")))
app = FastAPI(title="AgencyFlow AI", version="0.1.0", docs_url=None, redoc_url=None)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        intake_key TEXT NOT NULL UNIQUE,
        client_name TEXT NOT NULL,
        brief TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'review',
        created_at TEXT NOT NULL,
        approved_at TEXT,
        sent_at TEXT
    )""")
    db.commit()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_code(code: str | None):
    required = os.getenv("DEMO_ACCESS_CODE", "")
    if required:
        import secrets
        if not code or not secrets.compare_digest(code, required):
            raise HTTPException(status_code=401, detail="Enter the demo access code to use this workspace.")


def run_obj(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "client_name": row["client_name"], "brief": row["brief"],
            "plan": json.loads(row["plan_json"]), "status": row["status"],
            "created_at": row["created_at"], "approved_at": row["approved_at"],
            "sent_at": row["sent_at"], "intake_key": row["intake_key"]}


def get_run(run_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Run not found")
    return run_obj(row)


class Intake(BaseModel):
    client_name: str = Field(default="", max_length=100)
    brief: str = Field(min_length=30, max_length=6000)


@app.get("/api/config")
def config():
    provider = os.getenv("AI_PROVIDER", "ollama").lower()
    return {"provider": provider,
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:2b") if provider == "ollama" else os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            "handoff_enabled": bool(os.getenv("N8N_WEBHOOK_URL")),
            "access_code_required": bool(os.getenv("DEMO_ACCESS_CODE"))}


@app.post("/api/analyze")
def analyze(intake: Intake, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    brief = intake.brief.strip()
    client = intake.client_name.strip()
    if len(brief) < 30:
        raise HTTPException(422, "Brief must be at least 30 characters")
    normalized = json.dumps([brief, client], ensure_ascii=False, separators=(",", ":"))
    key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with connect() as db:
        existing = db.execute("SELECT * FROM runs WHERE intake_key=?", (key,)).fetchone()
    if existing:
        return {"run": run_obj(existing), "reused": True}
    try:
        plan = generate_plan(brief, client)
    except ModelError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(intake_key,client_name,brief,plan_json,created_at) VALUES (?,?,?,?,?)",
                   (key, client, brief, plan.model_dump_json(), utcnow()))
        db.commit()
        row = db.execute("SELECT * FROM runs WHERE intake_key=?", (key,)).fetchone()
    return {"run": run_obj(row), "reused": False}


@app.get("/api/runs")
def list_runs(x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    with connect() as db:
        rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30").fetchall()
    return {"runs": [run_obj(r) for r in rows]}


@app.put("/api/runs/{run_id}/plan")
def edit_plan(run_id: int, plan: Plan, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    with connect() as db:
        changed = db.execute("UPDATE runs SET plan_json=? WHERE id=? AND status='review'",
                             (plan.model_dump_json(), run_id)).rowcount
        db.commit()
    if not changed:
        get_run(run_id)
        raise HTTPException(409, "Only unapproved plans can be edited")
    return {"run": get_run(run_id)}


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: int, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    with connect() as db:
        changed = db.execute("UPDATE runs SET status='approved', approved_at=? WHERE id=? AND status='review'",
                             (utcnow(), run_id)).rowcount
        db.commit()
    run = get_run(run_id)
    if not changed and run["status"] not in ("approved", "sent"):
        raise HTTPException(409, "Run cannot be approved")
    return {"run": run}


@app.get("/api/runs/{run_id}/export")
def export(run_id: int, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    run = get_run(run_id)
    if run["status"] == "review":
        raise HTTPException(409, "Approve this plan before exporting")
    return {"schema_version": "agencyflow.v1", "event_id": f"af-{run['id']}-{run['intake_key'][:12]}",
            "reviewed_at": run["approved_at"], "client_name": run["client_name"], "plan": run["plan"]}


@app.post("/api/runs/{run_id}/handoff")
def handoff(run_id: int, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    webhook = os.getenv("N8N_WEBHOOK_URL", "")
    if not webhook:
        raise HTTPException(409, "Configure N8N_WEBHOOK_URL before sending")
    from urllib.parse import urlsplit
    parsed = urlsplit(webhook)
    if parsed.scheme not in ("https", "http") or not parsed.netloc or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1")):
        raise HTTPException(500, "Webhook must use HTTPS, or HTTP on localhost")
    run = get_run(run_id)
    if run["status"] == "sent":
        return {"run": run, "reused": True}
    if run["status"] != "approved":
        raise HTTPException(409, "Review and approve before handoff")
    payload = {"schema_version": "agencyflow.v1", "event_id": f"af-{run['id']}-{run['intake_key'][:12]}",
               "reviewed_at": run["approved_at"], "client_name": run["client_name"], "plan": run["plan"]}
    request = URLRequest(webhook, data=json.dumps(payload).encode("utf-8"),
                         headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=12) as response:
            if not 200 <= response.status < 300:
                raise HTTPException(502, "n8n did not accept this handoff")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise HTTPException(502, "n8n handoff failed; no sent status was recorded. Check the workflow and retry manually.") from exc
    with connect() as db:
        db.execute("UPDATE runs SET status='sent', sent_at=? WHERE id=? AND status='approved'", (utcnow(), run_id))
        db.commit()
    return {"run": get_run(run_id), "reused": False}


app.mount("/assets", StaticFiles(directory=BASE / "static"), name="assets")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.exception_handler(Exception)
async def uncaught(_request: Request, _exc: Exception):
    # Exception details are intentionally not exposed in the browser.
    return JSONResponse(status_code=500, content={"detail": "An internal error occurred. Check the server logs."})
