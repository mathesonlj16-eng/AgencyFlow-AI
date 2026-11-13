"""Campaign discovery API tests: persistence, confirmation, lock, and auth."""
import json

import pytest
from fastapi.testclient import TestClient

import app


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB", tmp_path / "discovery.sqlite3")
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    example = {
        "campaign_name": "Winter menu",
        "summary": "Create a set of fictional restaurant marketing assets.",
        "deadline_mention": "next Friday",
        "deadline": None,
        "deliverables": [
            {"name": "Instagram reels", "channel": "Instagram", "quantity": 3, "notes": ""}
        ],
        "tasks": [
            {"title": "Confirm launch timing", "role": "account manager", "priority": "high",
             "detail": "Ask client for the real date", "days_before_deadline": 3, "due_date": None}
        ],
        "missing_inputs": ["What calendar date is next Friday?"],
        "risks": [],
    }
    with app.connect() as db:
        db.execute("""INSERT INTO runs(intake_key,client_name,brief,plan_json,created_at)
                      VALUES (?,?,?,?,?)""",
                   ("fake-key", "Fictional Restaurant", "Three reels next Friday.", json.dumps(example),
                    app.utcnow()))
        rid = db.execute("SELECT id FROM runs WHERE intake_key='fake-key'").fetchone()["id"]
    return TestClient(app.app), rid


def test_discovery_get_defaults_to_unknown_without_inference(prepared):
    client, rid = prepared
    r = client.get(f"/api/runs/{rid}/context")
    assert r.status_code == 200, r.text
    context = r.json()["context"]
    assert set(context) == {"business_goal", "target_audience", "primary_kpi",
                            "measurement_source", "baseline", "target", "budget", "platforms"}
    assert all(value == {"status": "unknown", "value": None, "provenance": None}
               for value in context.values())
    assert len(r.json()["questions"]) == 8


def test_explicit_review_persists_proposed_and_confirmed_separately(prepared):
    client, rid = prepared
    original = client.get(f"/api/runs/{rid}/context").json()["context"]
    original["primary_kpi"] = {"status": "proposed", "value": "Reservations", "provenance": "Suggestion"}
    original["business_goal"] = {"status": "client_confirmed", "value": "More weekday diners",
                                 "provenance": "Coordinator notes from client call"}
    saved = client.put(f"/api/runs/{rid}/context", json=original)
    assert saved.status_code == 200, saved.text
    assert saved.json()["changed"] is True
    assert len(saved.json()["questions"]) == 7
    restored = client.get(f"/api/runs/{rid}/context").json()["context"]
    assert restored == original
    second = client.put(f"/api/runs/{rid}/context", json=restored)
    assert second.status_code == 200 and second.json()["changed"] is False
    assert "campaign_context_updated" in [x["kind"] for x in client.get(f"/api/runs/{rid}/events").json()["events"]]


def test_unsupported_claims_and_confirmations_without_source_are_rejected(prepared):
    client, rid = prepared
    state = client.get(f"/api/runs/{rid}/context").json()["context"]
    state["baseline"] = {"status": "client_confirmed", "value": "50 bookings", "provenance": None}
    assert client.put(f"/api/runs/{rid}/context", json=state).status_code == 422
    state["baseline"] = {"status": "unknown", "value": "50 bookings", "provenance": None}
    assert client.put(f"/api/runs/{rid}/context", json=state).status_code == 422
    state.pop("baseline")
    state["roi"] = {"status": "client_confirmed", "value": "500%", "provenance": "made up"}
    assert client.put(f"/api/runs/{rid}/context", json=state).status_code == 422


def test_context_locks_with_plan_approval_and_does_not_mutate_plan(prepared):
    client, rid = prepared
    before = client.get(f"/api/runs/{rid}").json()["run"]["plan"]
    state = client.get(f"/api/runs/{rid}/context").json()["context"]
    state["business_goal"] = {"status": "client_confirmed", "value": "Awareness", "provenance": "Client brief"}
    assert client.put(f"/api/runs/{rid}/context", json=state).status_code == 200
    assert client.post(f"/api/runs/{rid}/approve").status_code == 200
    assert client.put(f"/api/runs/{rid}/context", json=state).status_code == 409
    after = client.get(f"/api/runs/{rid}").json()["run"]["plan"]
    assert before == after
    assert client.get(f"/api/runs/{rid}/context").json()["context"]["business_goal"]["value"] == "Awareness"


def test_access_code_is_checked_on_both_endpoints(prepared, monkeypatch):
    client, rid = prepared
    monkeypatch.setenv("DEMO_ACCESS_CODE", "some-demo-code")
    path = f"/api/runs/{rid}/context"
    assert client.get(path).status_code == 401
    assert client.put(path, json={}).status_code == 401
    assert client.get(path, headers={"X-Demo-Code": "some-demo-code"}).status_code == 200


def test_missing_run_returns_404_and_does_not_create_context(prepared):
    client, _ = prepared
    assert client.get("/api/runs/99999/context").status_code == 404
    assert client.put("/api/runs/99999/context", json={}).status_code == 404
