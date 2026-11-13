import json
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

import app
from ai_client import ModelError, normalize_model_response
from plan import Plan


TEST_PLAN = {
    "campaign_name": "Winter Menu Launch",
    "summary": "Produce social content for a fictional restaurant winter menu launch.",
    "deadline": None,
    "deliverables": [{"name": "Instagram Reel", "channel": "Instagram", "quantity": 3, "notes": "Use footage after a shoot"}],
    "tasks": [
        {"title": "Confirm launch date", "role": "producer", "priority": "high", "detail": "Ask client for an absolute date"},
        {"title": "Organize photo and video shoot", "role": "producer", "priority": "high", "detail": "No video assets exist yet"},
        {"title": "Draft captions", "role": "copywriter", "priority": "medium", "detail": "Create three caption options"}
    ],
    "missing_inputs": ["What is the launch date?"],
    "risks": ["Required video footage has not been captured."]
}
BRIEF = "We need three social reels for our upcoming winter menu launch. No video assets have been filmed yet, and the exact deadline needs confirming."


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB", tmp_path / "agencyflow.sqlite3")
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    return TestClient(app.app)


def make_run(client):
    with patch.object(app, "generate_plan", return_value=Plan.model_validate(TEST_PLAN)):
        r = client.post("/api/analyze", json={"brief": BRIEF, "client_name": "Fictional Kitchen"})
    assert r.status_code == 200, r.text
    return r.json()["run"]


def test_model_contract_accepts_valid_and_rejects_unstructured():
    assert normalize_model_response(json.dumps(TEST_PLAN)).campaign_name == "Winter Menu Launch"
    assert normalize_model_response("```json\n" + json.dumps(TEST_PLAN) + "\n```").tasks[0].priority == "high"
    with pytest.raises(ModelError):
        normalize_model_response("Here is your plan: maybe make some posts")


def test_model_rejects_fabricated_or_bad_deadline():
    plan = {**TEST_PLAN, "deadline": "next Friday"}
    with pytest.raises(ModelError):
        normalize_model_response(json.dumps(plan))


def test_duplicate_brief_reuses_single_row_and_does_not_call_model_again(client):
    first = make_run(client)
    with patch.object(app, "generate_plan", side_effect=AssertionError("Should not call model")):
        response = client.post("/api/analyze", json={"brief": BRIEF, "client_name": "Fictional Kitchen"})
    assert response.status_code == 200
    assert response.json()["reused"] is True
    assert response.json()["run"]["id"] == first["id"]
    assert len(client.get("/api/runs").json()["runs"]) == 1


def test_approve_then_export_only_after_review(client):
    run = make_run(client)
    rid = run["id"]
    assert client.get(f"/api/runs/{rid}/export").status_code == 409
    updated = dict(TEST_PLAN)
    updated["campaign_name"] = "Revised Winter Launch"
    assert client.put(f"/api/runs/{rid}/plan", json=updated).status_code == 200
    approved = client.post(f"/api/runs/{rid}/approve")
    assert approved.status_code == 200
    assert approved.json()["run"]["status"] == "approved"
    assert client.put(f"/api/runs/{rid}/plan", json=TEST_PLAN).status_code == 409
    result = client.get(f"/api/runs/{rid}/export")
    assert result.status_code == 200
    assert result.json()["schema_version"] == "agencyflow.v1"
    assert result.json()["plan"]["campaign_name"] == "Revised Winter Launch"


def test_webhook_does_not_send_unapproved_plan(client, monkeypatch):
    run = make_run(client)
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://example.test/webhook/agencyflow-reviewed")
    with patch.object(app, "urlopen", side_effect=AssertionError("No outgoing request permitted")):
        response = client.post(f"/api/runs/{run['id']}/handoff")
    assert response.status_code == 409


def test_webhook_after_approval_and_replay_suppression(client, monkeypatch):
    run = make_run(client)
    rid = run["id"]
    client.post(f"/api/runs/{rid}/approve")
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://example.test/webhook/agencyflow-reviewed")
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    with patch.object(app, "urlopen", return_value=fake) as post:
        first = client.post(f"/api/runs/{rid}/handoff")
        second = client.post(f"/api/runs/{rid}/handoff")
    assert first.status_code == 200
    assert second.json()["reused"] is True
    assert post.call_count == 1
    payload = json.loads(post.call_args.args[0].data)
    assert payload["schema_version"] == "agencyflow.v1"
    assert payload["plan"]["tasks"][0]["title"] == "Confirm launch date"


def test_failed_handoff_preserves_approved_state(client, monkeypatch):
    run = make_run(client)
    rid = run["id"]
    client.post(f"/api/runs/{rid}/approve")
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://example.test/webhook/agencyflow-reviewed")
    from urllib.error import URLError
    with patch.object(app, "urlopen", side_effect=URLError("temporary failure")):
        response = client.post(f"/api/runs/{rid}/handoff")
    assert response.status_code == 502
    assert client.get("/api/runs").json()["runs"][0]["status"] == "approved"


def test_public_backend_requires_access_code_when_configured(client, monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "correct-example-code")
    assert client.get("/api/runs").status_code == 401
    assert client.post("/api/analyze", json={"brief": BRIEF}).status_code == 401
    assert client.get("/api/runs", headers={"X-Demo-Code": "incorrect"}).status_code == 401
    assert client.get("/api/runs", headers={"X-Demo-Code": "correct-example-code"}).status_code == 200


def test_rejects_public_http_webhook(client, monkeypatch):
    run = make_run(client)
    client.post(f"/api/runs/{run['id']}/approve")
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://external.example/webhook")
    with patch.object(app, "urlopen", side_effect=AssertionError("Unsafe webhook")):
        result = client.post(f"/api/runs/{run['id']}/handoff")
    assert result.status_code == 500
