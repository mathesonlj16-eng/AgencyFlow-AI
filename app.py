"""AgencyFlow AI: local-first creative operations assistant.

Run: uvicorn app:app --host 127.0.0.1 --port 8766
Requires Python 3.11+. Do not expose publicly without DEMO_ACCESS_CODE.

Run lifecycle:  review -> approved -> sending -> sent
                                  ^-----------'  (send failed: back to approved, retry allowed)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request as URLRequest, urlopen

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_client import ModelError, generate_plan, provider_settings
from plan import Plan, schedule_tasks
from review_diff import diff_plans, summarize
from brief_checks import quantity_warnings

BASE = Path(__file__).resolve().parent
DB = Path(os.getenv("DB_PATH", str(BASE / "data" / "agencyflow.sqlite3")))
SCHEMA_VERSION = "agencyflow.v1"
app = FastAPI(title="AgencyFlow AI", version="0.2.0", docs_url=None, redoc_url=None)

RUN_COLUMNS = {  # added after v0.1; migrated in place on startup
    "model": "TEXT", "attempts": "INTEGER", "latency_ms": "INTEGER",
    "handoff_attempts": "INTEGER NOT NULL DEFAULT 0", "last_error": "TEXT", "handoff_response": "TEXT",
    "sending_since": "TEXT", "ai_plan_json": "TEXT",
}
STALE_SENDING_SECONDS = 120  # a 'sending' claim older than this (e.g. server crashed mid-send) may be retried


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
    existing = {r["name"] for r in db.execute("PRAGMA table_info(runs)")}
    for column, ddl in RUN_COLUMNS.items():
        if column not in existing:
            db.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")
    # Append-only audit trail, including failures that never produced a run.
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER,
        intake_key TEXT,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
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


def log_event(db: sqlite3.Connection, kind: str, detail: str = "", run_id: int | None = None, intake_key: str | None = None):
    db.execute("INSERT INTO events(run_id,intake_key,kind,detail,created_at) VALUES (?,?,?,?,?)",
               (run_id, intake_key, kind, detail[:1000], utcnow()))


def check_code(code: str | None):
    required = os.getenv("DEMO_ACCESS_CODE", "")
    if required and (not code or not secrets.compare_digest(code, required)):
        raise HTTPException(status_code=401, detail="Enter the demo access code to use this workspace.")


def event_id_for(run_id: int, intake_key: str, created_at: str) -> str:
    """Stable per run, and unique across databases: created_at is mixed in so a reset
    database cannot reuse an id the n8n receiver has already seen."""
    digest = hashlib.sha256(f"{intake_key}|{created_at}".encode()).hexdigest()[:12]
    return f"af-{run_id}-{digest}"


def event_id(run: dict[str, Any]) -> str:
    return event_id_for(run["id"], run["intake_key"], run["created_at"])


def run_obj(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    plan = json.loads(row["plan_json"])
    return {"id": row["id"], "client_name": row["client_name"], "brief": row["brief"],
            "plan": plan, "status": row["status"],
            "warnings": quantity_warnings(row["brief"], plan),  # recomputed after every edit
            "created_at": row["created_at"], "approved_at": row["approved_at"], "sent_at": row["sent_at"],
            "intake_key": row["intake_key"],
            "model": row["model"] if "model" in keys else None,
            "attempts": row["attempts"] if "attempts" in keys else None,
            "latency_ms": row["latency_ms"] if "latency_ms" in keys else None,
            "handoff_attempts": row["handoff_attempts"] if "handoff_attempts" in keys else 0,
            "last_error": row["last_error"] if "last_error" in keys else None,
            "event_id": event_id_for(row["id"], row["intake_key"], row["created_at"]),
            "handoff_response": json.loads(row["handoff_response"]) if "handoff_response" in keys and row["handoff_response"] else None}


def get_run(run_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Run not found")
    return run_obj(row)


def intake_key(brief: str, client: str) -> str:
    """Duplicate detection that ignores case and whitespace differences."""
    norm = lambda s: re.sub(r"\s+", " ", s).strip().casefold()
    normalized = json.dumps([norm(brief), norm(client)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def handoff_payload(run: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "event_id": event_id(run), "run_id": run["id"],
            "reviewed_at": run["approved_at"], "client_name": run["client_name"], "plan": run["plan"]}


class Intake(BaseModel):
    client_name: str = Field(default="", max_length=100)
    brief: str = Field(min_length=30, max_length=6000)


@app.get("/api/config")
def config():
    provider, model = provider_settings()
    return {"provider": provider, "model": model,
            "handoff_enabled": bool(os.getenv("N8N_WEBHOOK_URL")),
            "access_code_required": bool(os.getenv("DEMO_ACCESS_CODE"))}


@app.get("/api/health")
def health():
    """Reports whether the configured model is actually available (no inference is run)."""
    provider, model = provider_settings()
    if provider != "ollama":
        return {"provider": provider, "model": model, "model_available": None}
    base = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat").rsplit("/api/", 1)[0]
    try:
        with urlopen(base + "/api/tags", timeout=3) as response:
            names = {m.get("name") for m in json.loads(response.read()).get("models", [])}
        return {"provider": provider, "model": model, "model_available": model in names or f"{model}:latest" in names}
    except (URLError, OSError, ValueError):
        return {"provider": provider, "model": model, "model_available": False, "detail": "Ollama is not reachable"}


@app.post("/api/analyze")
def analyze(intake: Intake, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    brief = intake.brief.strip()
    client = intake.client_name.strip()
    if len(brief) < 30:
        raise HTTPException(422, "Brief must be at least 30 characters")
    key = intake_key(brief, client)
    with connect() as db:
        existing = db.execute("SELECT * FROM runs WHERE intake_key=?", (key,)).fetchone()
        if existing:
            log_event(db, "duplicate_reused", "Same brief and client; returned the saved plan without calling the model.", existing["id"], key)
    if existing:
        return {"run": run_obj(existing), "reused": True}
    try:
        generation = generate_plan(brief, client)
    except ModelError as exc:
        with connect() as db:
            log_event(db, "generation_failed", str(exc), None, key)
        raise HTTPException(503, detail=str(exc)) from exc
    plan = schedule_tasks(generation.plan)
    with connect() as db:
        # INSERT OR IGNORE: if a concurrent identical request won the race, keep its row.
        # ai_plan_json is written once and never edited: it is the baseline for review stats.
        cur = db.execute("""INSERT OR IGNORE INTO runs(intake_key,client_name,brief,plan_json,ai_plan_json,created_at,model,attempts,latency_ms)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                         (key, client, brief, plan.model_dump_json(), plan.model_dump_json(), utcnow(), generation.model, generation.attempts, generation.latency_ms))
        row = db.execute("SELECT * FROM runs WHERE intake_key=?", (key,)).fetchone()
        if cur.rowcount:
            for err in generation.errors:
                log_event(db, "model_output_repaired", err, row["id"], key)
            for fix in generation.fixes:
                log_event(db, "rule_fix", fix, row["id"], key)
            log_event(db, "generated", f"{generation.provider}/{generation.model}, attempt {generation.attempts}, {generation.latency_ms} ms", row["id"], key)
    return {"run": run_obj(row), "reused": not cur.rowcount}


@app.get("/api/runs")
def list_runs(x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    with connect() as db:
        rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30").fetchall()
    return {"runs": [run_obj(r) for r in rows]}


@app.get("/api/runs/{run_id}")
def read_run(run_id: int, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    return {"run": get_run(run_id)}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: int, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    run = get_run(run_id)
    with connect() as db:
        rows = db.execute("SELECT kind,detail,created_at FROM events WHERE run_id=? OR (run_id IS NULL AND intake_key=?) ORDER BY id",
                          (run_id, run["intake_key"])).fetchall()
    return {"events": [dict(r) for r in rows]}


@app.get("/api/runs/{run_id}/review")
def review(run_id: int, x_demo_code: str | None = Header(default=None)):
    """What the reviewer changed compared with the AI's original draft."""
    check_code(x_demo_code)
    with connect() as db:
        row = db.execute("SELECT ai_plan_json, plan_json FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Run not found")
    if not row["ai_plan_json"]:
        return {"available": False}
    return {"available": True, "diff": diff_plans(json.loads(row["ai_plan_json"]), json.loads(row["plan_json"]))}


@app.get("/api/stats")
def stats(x_demo_code: str | None = Header(default=None)):
    """How much of the AI's work survives human review, across approved plans."""
    check_code(x_demo_code)
    with connect() as db:
        rows = db.execute("SELECT ai_plan_json, plan_json FROM runs WHERE status IN ('approved','sent') AND ai_plan_json IS NOT NULL").fetchall()
        generated = db.execute("SELECT COUNT(*) AS n, AVG(latency_ms) AS ms, SUM(attempts > 1) AS repaired FROM runs WHERE ai_plan_json IS NOT NULL").fetchone()
        failures = db.execute("SELECT COUNT(*) AS n FROM events WHERE kind='generation_failed'").fetchone()
    summary = summarize([diff_plans(json.loads(r["ai_plan_json"]), json.loads(r["plan_json"])) for r in rows])
    summary.update({"generated_runs": generated["n"], "avg_generation_seconds": round((generated["ms"] or 0) / 1000),
                    "runs_needing_repair": generated["repaired"] or 0, "generation_failures": failures["n"]})
    return summary


@app.put("/api/runs/{run_id}/plan")
def edit_plan(run_id: int, plan: Plan, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    plan = schedule_tasks(plan)
    new_json = plan.model_dump_json()
    with connect() as db:
        row = db.execute("SELECT plan_json FROM runs WHERE id=? AND status='review'", (run_id,)).fetchone()
        if row is not None and row["plan_json"] == new_json:
            return {"run": get_run(run_id)}  # no-op save: nothing to record
        changed = db.execute("UPDATE runs SET plan_json=? WHERE id=? AND status='review'", (new_json, run_id)).rowcount
        if changed:
            log_event(db, "edited", f"{len(plan.tasks)} tasks, deadline {plan.deadline or 'unconfirmed'}", run_id)
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
        if changed:
            log_event(db, "approved", "Human approval recorded; plan is now locked.", run_id)
    run = get_run(run_id)
    if not changed and run["status"] == "review":
        raise HTTPException(409, "Run cannot be approved")
    return {"run": run}


@app.get("/api/runs/{run_id}/export")
def export(run_id: int, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    run = get_run(run_id)
    if run["status"] == "review":
        raise HTTPException(409, "Approve this plan before exporting")
    return handoff_payload(run)


def _validated_webhook() -> str:
    webhook = os.getenv("N8N_WEBHOOK_URL", "")
    if not webhook:
        raise HTTPException(409, "Configure N8N_WEBHOOK_URL before sending")
    parsed = urlsplit(webhook)
    if parsed.scheme not in ("https", "http") or not parsed.netloc or (
            parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1")):
        raise HTTPException(500, "Webhook must use HTTPS, or HTTP on localhost")
    return webhook


@app.post("/api/runs/{run_id}/handoff")
def handoff(run_id: int, x_demo_code: str | None = Header(default=None)):
    check_code(x_demo_code)
    webhook = _validated_webhook()
    run = get_run(run_id)
    if run["status"] == "sent":
        with connect() as db:
            log_event(db, "handoff_replay_blocked", "Already delivered; no request sent.", run_id)
        return {"run": run, "reused": True}
    if run["status"] == "review":
        raise HTTPException(409, "Review and approve before handoff")
    # Claim the run so two clicks or tabs cannot send concurrently. A stale claim left by a
    # crash can be re-claimed; the receiver de-duplicates by event_id, so a resend is safe.
    stale_before = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - STALE_SENDING_SECONDS, timezone.utc).isoformat(timespec="seconds")
    with connect() as db:
        claimed = db.execute("""UPDATE runs SET status='sending', sending_since=?, handoff_attempts=handoff_attempts+1
                                WHERE id=? AND (status='approved' OR (status='sending' AND sending_since < ?))""",
                             (utcnow(), run_id, stale_before)).rowcount
    if not claimed:
        raise HTTPException(409, "A handoff for this plan is already in progress")
    payload = handoff_payload(run)
    request = URLRequest(webhook, data=json.dumps(payload).encode("utf-8"),
                         headers={"Content-Type": "application/json", "Idempotency-Key": payload["event_id"]}, method="POST")
    try:
        with urlopen(request, timeout=int(os.getenv("HANDOFF_TIMEOUT", "15"))) as response:
            if not 200 <= response.status < 300:
                raise HTTPError(webhook, response.status, "non-2xx", response.headers, None)
            body = response.read(100_000)
        try:
            ack = json.loads(body) if body else {}
        except ValueError:
            ack = {"raw": body[:500].decode("utf-8", "replace")}
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        if isinstance(exc, HTTPError):
            try:
                reason = json.loads(exc.read() or b"{}").get("error") or f"HTTP {exc.code}"
            except Exception:
                reason = f"HTTP {exc.code}"
            message = f"n8n rejected the handoff ({reason})."
        else:
            message = "n8n could not be reached."
        with connect() as db:
            db.execute("UPDATE runs SET status='approved', last_error=? WHERE id=? AND status='sending'", (message, run_id))
            log_event(db, "handoff_failed", message, run_id)
        raise HTTPException(502, f"{message} Plan stays approved; nothing was marked sent. Retry when n8n is available.") from exc
    with connect() as db:
        db.execute("UPDATE runs SET status='sent', sent_at=?, last_error=NULL, handoff_response=? WHERE id=? AND status='sending'",
                   (utcnow(), json.dumps(ack)[:5000], run_id))
        log_event(db, "handoff_sent", f"n8n acknowledged {payload['event_id']}" + (" (receiver reported duplicate)" if ack.get("duplicate") else ""), run_id)
    return {"run": get_run(run_id), "reused": False, "ack": ack}


def _tracker_url() -> str:
    """Read-back webhook for tasks n8n stored. Defaults to the sibling path of the handoff webhook."""
    explicit = os.getenv("N8N_TASKS_URL", "")
    if explicit:
        return explicit
    hook = os.getenv("N8N_WEBHOOK_URL", "")
    return hook.replace("/agencyflow-reviewed", "/agencyflow-tasks") if hook.endswith("/agencyflow-reviewed") else ""


@app.get("/api/tracker")
def tracker(event_id: str | None = None, x_demo_code: str | None = Header(default=None)):
    """Tasks as stored in n8n (read-only). Proves the handoff landed somewhere, not just that n8n said 200."""
    check_code(x_demo_code)
    url = _tracker_url()
    if not url:
        raise HTTPException(409, "No n8n task read-back URL configured")
    _validated_webhook()  # same HTTPS / localhost rule
    try:
        with urlopen(URLRequest(url, method="GET"), timeout=10) as response:
            tasks = json.loads(response.read(500_000) or b"{}").get("tasks", [])
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise HTTPException(502, "Could not read tasks back from n8n.") from exc
    keep = ("external_ref", "event_id", "campaign", "title", "assignee_role", "priority", "due_date", "status", "received_at")
    rows = [{k: t.get(k) for k in keep} for t in tasks if isinstance(t, dict) and (not event_id or t.get("event_id") == event_id)]
    return {"tasks": rows}


app.mount("/assets", StaticFiles(directory=BASE / "static"), name="assets")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", [])[1:]) or "request"
    return JSONResponse(status_code=422, content={"detail": f"Check {where}: {first.get('msg', 'invalid value')}"})


@app.exception_handler(Exception)
async def uncaught(_request: Request, _exc: Exception):
    # Exception details are intentionally not exposed in the browser.
    return JSONResponse(status_code=500, content={"detail": "An internal error occurred. Check the server logs."})
