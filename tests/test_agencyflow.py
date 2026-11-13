import json
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
import io

import pytest
from fastapi.testclient import TestClient

import ai_client
import app
from ai_client import Generation, ModelError, normalize_model_response
from plan import Plan, model_output_schema, schedule_tasks


TEST_PLAN = {
    "campaign_name": "Winter Menu Launch",
    "summary": "Produce social content for a fictional restaurant winter menu launch.",
    "deadline_mention": "next Friday",
    "deadline": None,
    "deliverables": [{"name": "Instagram Reel", "channel": "Instagram", "quantity": 3, "notes": "Use footage after a shoot"}],
    "tasks": [
        {"title": "Confirm launch date", "role": "producer", "priority": "high", "detail": "Ask client for an absolute date", "days_before_deadline": 7},
        {"title": "Organize photo and video shoot", "role": "producer", "priority": "high", "detail": "No video assets exist yet", "days_before_deadline": 5},
        {"title": "Draft captions", "role": "copywriter", "priority": "medium", "detail": "Create three caption options", "days_before_deadline": 2}
    ],
    "missing_inputs": ["What is the launch date?"],
    "risks": ["Required video footage has not been captured."]
}
BRIEF = "We need three social reels for our upcoming winter menu launch. No video assets have been filmed yet, and the exact deadline needs confirming."
HOOK = "https://example.test/webhook/agencyflow-reviewed"


def fake_generation(plan=TEST_PLAN, attempts=1, errors=None):
    return Generation(Plan.model_validate(plan), "ollama", "test-model", attempts, 1234, errors or [])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB", tmp_path / "agencyflow.sqlite3")
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    return TestClient(app.app)


def make_run(client, brief=BRIEF, name="Fictional Kitchen"):
    with patch.object(app, "generate_plan", return_value=fake_generation()):
        r = client.post("/api/analyze", json={"brief": brief, "client_name": name})
    assert r.status_code == 200, r.text
    return r.json()["run"]


def ok_response(body: dict):
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    fake.__enter__.return_value.read.return_value = json.dumps(body).encode()
    return fake


def events(client, rid):
    return [e["kind"] for e in client.get(f"/api/runs/{rid}/events").json()["events"]]


# ---------- model contract ----------

def test_model_contract_accepts_valid_and_rejects_unstructured():
    assert normalize_model_response(json.dumps(TEST_PLAN)).campaign_name == "Winter Menu Launch"
    assert normalize_model_response("```json\n" + json.dumps(TEST_PLAN) + "\n```").tasks[0].priority == "high"
    with pytest.raises(ModelError):
        normalize_model_response("Here is your plan: maybe make some posts")


def test_model_rejects_fabricated_or_bad_deadline():
    plan = {**TEST_PLAN, "deadline": "next Friday"}
    with pytest.raises(ModelError):
        normalize_model_response(json.dumps(plan))


def test_model_supplied_due_dates_are_ignored():
    plan = json.loads(json.dumps(TEST_PLAN))
    plan["tasks"][0]["due_date"] = "1999-01-01"
    assert normalize_model_response(json.dumps(plan)).tasks[0].due_date is None


def test_quoted_iso_date_is_recovered_but_relative_words_are_not():
    quoted = {**TEST_PLAN, "deadline_mention": "first TikTok live by 2026-12-01", "deadline": None}
    assert normalize_model_response(json.dumps(quoted)).deadline == "2026-12-01"
    assert normalize_model_response(json.dumps(TEST_PLAN)).deadline is None  # "next Friday" stays unconfirmed
    two = {**TEST_PLAN, "deadline_mention": "2026-12-01 or 2026-12-08", "deadline": None}
    assert normalize_model_response(json.dumps(two)).deadline is None  # ambiguous: a person decides


def test_schema_sent_to_model_excludes_server_fields():
    schema = model_output_schema()
    assert "due_date" not in schema["$defs"]["Task"]["properties"]
    assert "days_before_deadline" in schema["$defs"]["Task"]["required"]


def test_due_dates_are_back_scheduled_deterministically():
    plan = schedule_tasks(Plan.model_validate({**TEST_PLAN, "deadline": "2026-12-04"}))
    assert [t.due_date for t in plan.tasks] == ["2026-11-27", "2026-11-29", "2026-12-02"]
    assert all(t.due_date is None for t in schedule_tasks(Plan.model_validate(TEST_PLAN)).tasks)


def test_invalid_output_gets_one_repair_attempt(monkeypatch):
    replies = iter(["not json at all", json.dumps(TEST_PLAN)])
    calls = []
    monkeypatch.setattr(ai_client, "_call", lambda p, m, msgs, t: calls.append(msgs) or next(replies))
    gen = ai_client.generate_plan(BRIEF)
    assert gen.attempts == 2 and len(gen.errors) == 1
    assert "rejected" in calls[1][-1]["content"]  # the model saw the validation error


def test_repeated_invalid_output_is_an_error_not_a_fake_plan(monkeypatch):
    monkeypatch.setattr(ai_client, "_call", lambda *a: '{"campaign_name": "x"}')
    with pytest.raises(ModelError) as exc:
        ai_client.generate_plan(BRIEF)
    assert exc.value.attempts == 2


# ---------- intake, duplicates, persistence ----------

def test_duplicate_brief_reuses_single_row_and_does_not_call_model_again(client):
    first = make_run(client)
    with patch.object(app, "generate_plan", side_effect=AssertionError("Should not call model")):
        response = client.post("/api/analyze", json={"brief": BRIEF, "client_name": "Fictional Kitchen"})
    assert response.status_code == 200
    assert response.json()["reused"] is True
    assert response.json()["run"]["id"] == first["id"]
    assert len(client.get("/api/runs").json()["runs"]) == 1


def test_duplicate_detection_ignores_case_and_whitespace(client):
    first = make_run(client)
    variant = "  " + BRIEF.upper().replace(" ", "   ") + "\n"
    with patch.object(app, "generate_plan", side_effect=AssertionError("Should not call model")):
        r = client.post("/api/analyze", json={"brief": variant, "client_name": "fictional kitchen "})
    assert r.json()["reused"] is True and r.json()["run"]["id"] == first["id"]


def test_model_failure_returns_503_and_is_logged_without_creating_a_run(client):
    with patch.object(app, "generate_plan", side_effect=ModelError("model down")):
        r = client.post("/api/analyze", json={"brief": BRIEF, "client_name": "X"})
    assert r.status_code == 503 and "model down" in r.json()["detail"]
    assert client.get("/api/runs").json()["runs"] == []
    # Recovery: the same brief succeeds on retry and its history shows the earlier failure.
    run = make_run(client, name="X")
    assert events(client, run["id"])[:2] == ["generation_failed", "generated"]


def test_runs_persist_metadata_and_survive_reconnect(client):
    run = make_run(client)
    stored = client.get(f"/api/runs/{run['id']}").json()["run"]
    assert stored["model"] == "test-model" and stored["attempts"] == 1 and stored["latency_ms"] == 1234
    assert stored["event_id"].startswith(f"af-{run['id']}-")


def test_event_id_differs_after_database_reset(tmp_path, monkeypatch, client):
    first = make_run(client)
    monkeypatch.setattr(app, "DB", tmp_path / "fresh.sqlite3")  # same brief, new database, same run id 1
    import time; time.sleep(1.1)  # created_at has second resolution
    second = make_run(client)
    assert first["id"] == second["id"] == 1
    assert first["event_id"] != second["event_id"]
    assert second["event_id"].startswith("af-1-")


def test_legacy_v01_database_is_migrated(tmp_path, monkeypatch):
    import sqlite3
    db_path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, intake_key TEXT NOT NULL UNIQUE,
        client_name TEXT NOT NULL, brief TEXT NOT NULL, plan_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'review',
        created_at TEXT NOT NULL, approved_at TEXT, sent_at TEXT)""")
    con.execute("INSERT INTO runs(intake_key,client_name,brief,plan_json,created_at) VALUES ('k','c','b',?, 'now')", (json.dumps(TEST_PLAN),))
    con.commit(); con.close()
    monkeypatch.setattr(app, "DB", db_path)
    runs = TestClient(app.app).get("/api/runs").json()["runs"]
    assert runs[0]["handoff_attempts"] == 0 and runs[0]["model"] is None


# ---------- review, edit, approval ----------

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


def test_edit_can_set_deadline_add_and_remove_tasks_and_recomputes_dates(client):
    rid = make_run(client)["id"]
    edited = json.loads(json.dumps(TEST_PLAN))
    edited["deadline"] = "2026-12-04"
    edited["tasks"].pop(2)
    edited["tasks"].append({"title": "Client review of drafts", "role": "producer", "priority": "high",
                            "detail": "", "days_before_deadline": 1, "due_date": "2000-01-01"})
    r = client.put(f"/api/runs/{rid}/plan", json=edited)
    assert r.status_code == 200
    tasks = r.json()["run"]["plan"]["tasks"]
    assert [t["title"] for t in tasks][-1] == "Client review of drafts"
    assert [t["due_date"] for t in tasks] == ["2026-11-27", "2026-11-29", "2026-12-03"]  # browser value ignored


def test_review_diff_reports_what_the_reviewer_changed(client):
    rid = make_run(client)["id"]
    edited = json.loads(json.dumps(TEST_PLAN))
    edited["deadline"] = "2026-12-04"
    edited["tasks"][1]["priority"] = "medium"                     # edited
    edited["tasks"].pop(2)                                         # removed "Draft captions"
    edited["tasks"].append({"title": "Book videographer", "role": "producer", "priority": "high", "detail": "", "days_before_deadline": 6})
    edited["missing_inputs"].append("Which dishes must appear?")
    client.put(f"/api/runs/{rid}/plan", json=edited)
    d = client.get(f"/api/runs/{rid}/review").json()["diff"]
    assert d["tasks"]["kept"] == 1 and d["tasks"]["ai_count"] == 3
    assert d["tasks"]["edited"] == [{"title": "Organize photo and video shoot", "changed": ["priority"]}]
    assert d["tasks"]["removed"] == ["Draft captions"] and d["tasks"]["added"] == ["Book videographer"]
    assert d["deadline"]["set_by_reviewer"] is True
    assert d["missing_inputs"]["added"] == ["Which dishes must appear?"]
    assert d["ai_tasks_kept_unchanged_pct"] == 33
    client.post(f"/api/runs/{rid}/approve")
    s = client.get("/api/stats").json()
    assert s["approved_runs"] == 1 and s["avg_ai_tasks_kept_unchanged_pct"] == 33
    assert s["runs_where_reviewer_set_deadline"] == 1 and s["generated_runs"] == 1


def test_unedited_plan_shows_everything_kept(client):
    rid = make_run(client)["id"]
    d = client.get(f"/api/runs/{rid}/review").json()["diff"]
    assert d["ai_tasks_kept_unchanged_pct"] == 100 and not d["tasks"]["added"] and not d["fields_changed"]


def test_invalid_edit_is_rejected_with_readable_message(client):
    rid = make_run(client)["id"]
    bad = {**TEST_PLAN, "tasks": []}
    r = client.put(f"/api/runs/{rid}/plan", json=bad)
    assert r.status_code == 422 and "tasks" in r.json()["detail"]


# ---------- n8n handoff ----------

def test_webhook_does_not_send_unapproved_plan(client, monkeypatch):
    run = make_run(client)
    monkeypatch.setenv("N8N_WEBHOOK_URL", HOOK)
    with patch.object(app, "urlopen", side_effect=AssertionError("No outgoing request permitted")):
        response = client.post(f"/api/runs/{run['id']}/handoff")
    assert response.status_code == 409


def test_webhook_after_approval_and_replay_suppression(client, monkeypatch):
    run = make_run(client)
    rid = run["id"]
    client.post(f"/api/runs/{rid}/approve")
    monkeypatch.setenv("N8N_WEBHOOK_URL", HOOK)
    with patch.object(app, "urlopen", return_value=ok_response({"status": "accepted", "duplicate": False})) as post:
        first = client.post(f"/api/runs/{rid}/handoff")
        second = client.post(f"/api/runs/{rid}/handoff")
    assert first.status_code == 200
    assert first.json()["run"]["handoff_response"]["status"] == "accepted"
    assert second.json()["reused"] is True
    assert post.call_count == 1
    req = post.call_args.args[0]
    payload = json.loads(req.data)
    assert payload["schema_version"] == "agencyflow.v1"
    assert payload["plan"]["tasks"][0]["title"] == "Confirm launch date"
    assert req.get_header("Idempotency-key") == payload["event_id"]
    assert events(client, rid)[-2:] == ["handoff_sent", "handoff_replay_blocked"]


def test_failed_handoff_preserves_approved_state_and_retry_succeeds(client, monkeypatch):
    run = make_run(client)
    rid = run["id"]
    client.post(f"/api/runs/{rid}/approve")
    monkeypatch.setenv("N8N_WEBHOOK_URL", HOOK)
    with patch.object(app, "urlopen", side_effect=URLError("temporary failure")):
        response = client.post(f"/api/runs/{rid}/handoff")
    assert response.status_code == 502
    stored = client.get(f"/api/runs/{rid}").json()["run"]
    assert stored["status"] == "approved" and "could not be reached" in stored["last_error"]
    with patch.object(app, "urlopen", return_value=ok_response({"status": "accepted"})):
        retry = client.post(f"/api/runs/{rid}/handoff")
    assert retry.status_code == 200
    final = retry.json()["run"]
    assert final["status"] == "sent" and final["handoff_attempts"] == 2 and final["last_error"] is None


def test_receiver_rejection_reason_is_surfaced(client, monkeypatch):
    rid = make_run(client)["id"]
    client.post(f"/api/runs/{rid}/approve")
    monkeypatch.setenv("N8N_WEBHOOK_URL", HOOK)
    err = HTTPError(HOOK, 422, "Unprocessable", {}, io.BytesIO(b'{"error": "plan has no tasks"}'))
    with patch.object(app, "urlopen", side_effect=err):
        r = client.post(f"/api/runs/{rid}/handoff")
    assert r.status_code == 502 and "plan has no tasks" in r.json()["detail"]
    assert client.get(f"/api/runs/{rid}").json()["run"]["status"] == "approved"


def test_concurrent_send_is_blocked_while_in_flight(client, monkeypatch):
    rid = make_run(client)["id"]
    client.post(f"/api/runs/{rid}/approve")
    monkeypatch.setenv("N8N_WEBHOOK_URL", HOOK)
    with app.connect() as db:  # simulate another request mid-send
        db.execute("UPDATE runs SET status='sending', sending_since=? WHERE id=?", (app.utcnow(), rid))
    with patch.object(app, "urlopen", side_effect=AssertionError("must not send twice")):
        r = client.post(f"/api/runs/{rid}/handoff")
    assert r.status_code == 409


def test_stale_sending_claim_can_be_recovered(client, monkeypatch):
    rid = make_run(client)["id"]
    client.post(f"/api/runs/{rid}/approve")
    monkeypatch.setenv("N8N_WEBHOOK_URL", HOOK)
    with app.connect() as db:  # server crashed mid-send long ago
        db.execute("UPDATE runs SET status='sending', sending_since='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
    with patch.object(app, "urlopen", return_value=ok_response({"status": "accepted"})):
        r = client.post(f"/api/runs/{rid}/handoff")
    assert r.status_code == 200 and r.json()["run"]["status"] == "sent"


def test_tracker_reads_back_only_this_plans_tasks(client, monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_URL", HOOK)
    stored = {"tasks": [{"external_ref": "af-1-aaa#1", "event_id": "af-1-aaa", "title": "Mine", "secret_extra": "x"},
                        {"external_ref": "af-2-bbb#1", "event_id": "af-2-bbb", "title": "Other"}]}
    with patch.object(app, "urlopen", return_value=ok_response(stored)) as get:
        r = client.get("/api/tracker", params={"event_id": "af-1-aaa"})
    assert get.call_args.args[0].full_url == "https://example.test/webhook/agencyflow-tasks"
    assert r.status_code == 200 and [t["title"] for t in r.json()["tasks"]] == ["Mine"]
    assert "secret_extra" not in r.json()["tasks"][0]


def test_tracker_unconfigured_or_unreachable(client, monkeypatch):
    assert client.get("/api/tracker").status_code == 409
    monkeypatch.setenv("N8N_WEBHOOK_URL", HOOK)
    with patch.object(app, "urlopen", side_effect=URLError("down")):
        assert client.get("/api/tracker").status_code == 502


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
