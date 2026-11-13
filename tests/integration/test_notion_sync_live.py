"""Live integration tests: real n8n workflow + local Notion mock (no Notion account, no internet).

Run:   $env:AGENCYFLOW_LIVE = '1'; .venv\\Scripts\\python.exe -m pytest tests/integration -q
Needs: scripts/start-demo-n8n.ps1 done, and the mock running:
       docker run -d --name notion-mock --network agencyflow-net -p 127.0.0.1:8787:8787 \
         -v <repo>/tests/notion_mock:/mock:ro --entrypoint node docker.n8n.io/n8nio/n8n:2.47.4 /mock/server.js
These tests swap the demo n8n's Notion credential for a mock token and repoint its settings at the mock.
They refuse to run once scripts/notion-connect.ps1 has stored a real token (it leaves a marker file).
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.getenv("AGENCYFLOW_LIVE") != "1", reason="set AGENCYFLOW_LIVE=1 to run live n8n tests")
N8N = "http://localhost:5679/webhook"
MOCK = "http://127.0.0.1:8787"
MOCK_API = "http://notion-mock:8787/v1"


def call(method, url, body=None, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def set_credential(token: str):
    cred = [{"id": "AgFlowNotionCred1", "name": "AgencyFlow Notion (sandbox)", "type": "httpHeaderAuth",
             "data": {"name": "Authorization", "value": f"Bearer {token}"}}]
    subprocess.run(["docker", "exec", "-i", "agencyflow-n8n", "sh", "-c",
                    "cat > /home/node/.n8n/c.json && n8n import:credentials --input=/home/node/.n8n/c.json >/dev/null; rm -f /home/node/.n8n/c.json"],
                   input=json.dumps(cred).encode(), check=True, capture_output=True)


def mock_state():
    return call("GET", f"{MOCK}/__state")[1]


def inject(**failure):
    call("POST", f"{MOCK}/__fail", failure)


def new_event():
    return f"af-{random.randint(1000, 99999)}-{random.getrandbits(48):012x}"


def send_plan(event_id, n=2, campaign="Live test campaign"):
    tasks = [{"title": f"Task {i + 1}", "role": ["producer", "editor", "designer"][i % 3], "priority": ["high", "medium", "low"][i % 3],
              "detail": f"Detail {i + 1}", "due_date": f"2026-12-0{i + 1}"} for i in range(n)]
    payload = {"schema_version": "agencyflow.v1", "event_id": event_id, "run_id": 1, "reviewed_at": "2026-11-13T06:00:00+00:00",
               "client_name": "Live Test Client (fictional)", "plan": {"campaign_name": campaign, "summary": "s", "tasks": tasks}}
    status, body = call("POST", f"{N8N}/agencyflow-reviewed", payload)
    assert status == 200 and body["status"] == "accepted", body
    return payload


def sync(event_id):
    return call("POST", f"{N8N}/agencyflow-notion-sync", {"event_id": event_id})


def pages_for(event_id):
    txt = lambda p: "".join(t["plain_text"] for t in p["properties"].get("Event ID", {}).get("rich_text", []))
    return [p for p in mock_state()["pages"] if txt(p) == event_id]


@pytest.fixture(scope="module", autouse=True)
def sandbox():
    marker = subprocess.run(["docker", "exec", "agencyflow-n8n", "test", "-f", "/home/node/.n8n/agencyflow-real-notion"])
    if marker.returncode == 0:
        pytest.skip("This n8n holds a real Notion credential; live mock tests would overwrite it.")
    call("POST", f"{MOCK}/__reset")
    set_credential("mock-test-token")
    status, body = call("POST", f"{N8N}/agencyflow-notion-setup", {"parent_page_id": "0" * 32, "api_base": MOCK_API, "enable": True})
    assert status == 200 and body["status"] == "configured", body
    yield body["data_source_id"]
    set_credential("not-configured")  # leave the placeholder behind, never a usable token


@pytest.fixture(autouse=True)
def clean_failures():
    call("POST", f"{MOCK}/__clear_failures")
    yield


def test_disabled_sync_writes_nothing(sandbox):
    call("POST", f"{N8N}/agencyflow-notion-setup", {"data_source_id": sandbox, "api_base": MOCK_API, "enable": False})
    ev = new_event(); send_plan(ev)
    before = len(mock_state()["pages"])
    status, body = sync(ev)
    assert status == 409 and body["status"] == "disabled"
    assert len(mock_state()["pages"]) == before
    call("POST", f"{N8N}/agencyflow-notion-setup", {"data_source_id": sandbox, "api_base": MOCK_API, "enable": True})


def test_setup_refuses_non_notion_endpoints():
    status, body = call("POST", f"{N8N}/agencyflow-notion-setup", {"data_source_id": "0" * 32, "api_base": "https://evil.example/v1", "enable": True})
    assert status == 422 and "not an allowed" in body["error"]


def test_only_approved_stored_tasks_can_sync():
    assert sync("not-an-event")[0] == 422
    status, body = sync(new_event())  # well-formed, but never approved or stored
    assert status == 404 and body["status"] == "not_found"


def test_full_sync_maps_fields_and_reads_back():
    ev = new_event(); send_plan(ev, n=3, campaign="Field map check")
    status, body = sync(ev)
    assert status == 200 and body["status"] == "complete" and body["synced"] == 3, body
    pages = pages_for(ev)
    assert len(pages) == 3
    p = next(p for p in pages if p["properties"]["External ref"]["rich_text"][0]["plain_text"] == f"{ev}#2")
    props = p["properties"]
    assert props["Name"]["title"][0]["plain_text"] == "Task 2"
    assert props["Role"]["rich_text"][0]["plain_text"] == "editor"
    assert props["Priority"]["select"]["name"] == "medium"
    assert props["Due"]["date"]["start"] == "2026-12-02"
    assert props["Campaign"]["rich_text"][0]["plain_text"] == "Field map check"
    assert props["Client"]["rich_text"][0]["plain_text"] == "Live Test Client (fictional)"
    assert props["Status"]["select"]["name"] == "To do"
    assert all(t["url"].startswith("https://") for t in body["tasks"])


def test_resend_replay_and_tamper_never_duplicate():
    ev = new_event(); payload = send_plan(ev, n=2)
    assert sync(ev)[1]["status"] == "complete"
    second = sync(ev)[1]
    assert second["status"] == "complete" and {t["action"] for t in second["tasks"]} == {"update"}
    assert call("POST", f"{N8N}/agencyflow-reviewed", payload)[1]["status"] == "duplicate_ignored"
    payload["plan"]["tasks"][0]["title"] = "Tampered"
    assert call("POST", f"{N8N}/agencyflow-reviewed", payload)[0] == 409
    assert sync(ev)[1]["status"] == "complete"
    assert len(pages_for(ev)) == 2


def test_partial_failure_then_retry_fixes_only_the_gap():
    ev = new_event(); send_plan(ev, n=3)
    inject(op="create", status=500, code="internal_server_error", skip=1, remaining=1)  # 2nd create fails, no auto-retry
    status, body = sync(ev)
    assert body["status"] == "partial" and body["synced"] == 2, body
    failed = [t for t in body["tasks"] if t["status"] == "failed"]
    assert len(failed) == 1 and "server error" in failed[0]["error"]
    assert len(pages_for(ev)) == 2
    retry = sync(ev)[1]
    assert retry["status"] == "complete" and retry["synced"] == 3
    actions = sorted(t["action"] for t in retry["tasks"])
    assert actions == ["create", "update", "update"]  # only the missing task is created
    assert len(pages_for(ev)) == 3


def test_rate_limited_read_is_retried_automatically():
    ev = new_event(); send_plan(ev, n=2)
    inject(op="query", status=429, code="rate_limited", remaining=1)
    status, body = sync(ev)
    assert body["status"] == "complete", body


def test_lost_create_response_does_not_duplicate():
    ev = new_event(); send_plan(ev, n=2)
    inject(op="create_lost", remaining=1)  # Notion stores the page, but the reply never arrives
    body = sync(ev)[1]
    assert body["status"] == "complete", body  # read-back finds the page, so it counts as synced
    assert len(pages_for(ev)) == 2
    assert sync(ev)[1]["status"] == "complete" and len(pages_for(ev)) == 2


def test_permission_error_creates_nothing_and_explains():
    ev = new_event(); send_plan(ev, n=2)
    calls_before = len(mock_state()["calls"])
    inject(op="query", status=403, code="restricted_resource", message="Insufficient permissions", remaining=6)  # 2 finds x 3 tries
    body = sync(ev)[1]
    assert body["status"] == "failed"
    assert all("permission" in t["error"].lower() for t in body["tasks"])
    new_calls = mock_state()["calls"][calls_before:]
    assert "POST /v1/pages" not in new_calls  # never create when absence could not be confirmed
    assert sync(ev)[1]["status"] == "complete" and len(pages_for(ev)) == 2  # after access is fixed


def test_missing_or_bad_credential_is_explained():
    ev = new_event(); send_plan(ev, n=1)
    set_credential("not-configured")
    try:
        body = sync(ev)[1]
        assert body["status"] == "failed" and "credential" in body["tasks"][0]["error"].lower()
        assert pages_for(ev) == []
    finally:
        set_credential("mock-test-token")
    assert sync(ev)[1]["status"] == "complete"


def test_resync_repairs_edited_fields_but_keeps_reviewer_status():
    ev = new_event(); send_plan(ev, n=1)
    assert sync(ev)[1]["status"] == "complete"
    call("POST", f"{MOCK}/__edit", {"external_ref": f"{ev}#1",
                                    "properties": {"Priority": {"select": {"name": "low"}}, "Status": {"select": {"name": "Done"}}}})
    assert sync(ev)[1]["status"] == "complete"
    props = pages_for(ev)[0]["properties"]
    assert props["Priority"]["select"]["name"] == "high"   # AgencyFlow-owned field restored
    assert props["Status"]["select"]["name"] == "Done"     # the team's progress in Notion is left alone
