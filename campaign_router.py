"""Review-only campaign discovery API, kept separate from the AI plan/n8n contract.

The context is intentionally optional: missing values are UNKNOWN, not model guesses.
This router can be integrated without modifying the existing plan or receiver schemas.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from campaign_context import CampaignContext, DiscoveryStatus

router = APIRouter()


def _app():
    # Imported only at request time to avoid a cycle with app.include_router().
    import app
    return app


def _ensure_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS campaign_context (
            run_id INTEGER PRIMARY KEY,
            context_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)


def _questions(context: CampaignContext) -> list[str]:
    return context.unresolved_questions()


@router.get("/api/runs/{run_id}/context")
def read_campaign_context(run_id: int, x_demo_code: str | None = Header(default=None)):
    agency = _app()
    agency.check_code(x_demo_code)
    agency.get_run(run_id)  # same 404 semantics as the rest of the app
    with agency.connect() as db:
        _ensure_table(db)
        row = db.execute(
            "SELECT context_json FROM campaign_context WHERE run_id=?", (run_id,)
        ).fetchone()
    context = CampaignContext.model_validate_json(row["context_json"]) if row else CampaignContext()
    return {"context": context.model_dump(mode="json"), "questions": _questions(context)}


@router.put("/api/runs/{run_id}/context")
def save_campaign_context(
    run_id: int,
    context: CampaignContext,
    x_demo_code: str | None = Header(default=None),
):
    """Only an explicit, editable review action can mark information as confirmed.

    No AI inference, outbound messaging or task handoff occurs in this endpoint.
    """
    agency = _app()
    agency.check_code(x_demo_code)
    with agency.connect() as db:
        _ensure_table(db)
        # Write claim serializes changes with the existing approval update.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Run not found")
        if row["status"] != "review":
            raise HTTPException(409, "Campaign discovery is locked after plan approval")
        serialized = context.model_dump_json()
        old = db.execute(
            "SELECT context_json FROM campaign_context WHERE run_id=?", (run_id,)
        ).fetchone()
        if old and old["context_json"] == serialized:
            return {"context": context.model_dump(mode="json"), "questions": _questions(context), "changed": False}
        db.execute("""
            INSERT INTO campaign_context(run_id, context_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                context_json=excluded.context_json, updated_at=excluded.updated_at
        """, (run_id, serialized, agency.utcnow()))
        confirmed = sum(
            getattr(context, field).status == DiscoveryStatus.CLIENT_CONFIRMED
            for field in context.model_fields
        )
        agency.log_event(db, "campaign_context_updated",
                         f"Campaign context saved; {confirmed} field(s) explicitly confirmed.", run_id)
    return {"context": context.model_dump(mode="json"), "questions": _questions(context), "changed": True}
