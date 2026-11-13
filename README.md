# AgencyFlow AI

**From messy creative briefs to reviewable production plans.** A small, local-first portfolio project designed to demonstrate real LLM integration, structured validation, workflow design, and human approval.

> Independent demonstration built with fictional client briefs. It is **not** a paid client engagement, an official Beyond Creator project, or a substitute for an operational agency system.

## What works

- Paste a creative brief and generate a structured plan **using a real LLM** via your local Ollama instance or an OpenAI-compatible API.
- Pydantic validation requires deliverables, practical role-based tasks, missing inputs, and risks. Invalid model output is an explicit error, not a fabricated success.
- Save edits, approve a reviewed plan, export a versioned JSON payload, or send it to an **optional** n8n webhook after explicit approval.
- Duplicate briefs for the same client reuse one SQLite record instead of regenerating work.
- Public deployment safety option: protect API endpoints with `DEMO_ACCESS_CODE` so unknown visitors cannot consume paid model credits.
- Included n8n workflow receives approved payloads and returns a **read-only acknowledgment**. It intentionally does not send messages or create tasks.

## Run locally (Windows PowerShell)

Requirements: Python 3.11+ and a running Ollama installation with a suitable model.

```powershell
cd AgencyFlow-AI
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
ollama pull qwen3.5:2b    # skip if already installed
$env:AI_PROVIDER = 'ollama'
$env:OLLAMA_MODEL = 'qwen3.5:2b'
python -m uvicorn app:app --host 127.0.0.1 --port 8766
```

Or, on Windows, run `./run.ps1` from the project directory to create the environment and start the app.

Open **http://127.0.0.1:8766**. Choose a fictional sample brief, then **Generate production plan**. The model must actually be running; the application does not claim AI success when it is not.

The first attempt may be slow on a small local model. Smaller models may produce invalid JSON or poor recommendations: that is surfaced for human review. For a more capable cloud provider:

```powershell
$env:AI_PROVIDER = 'openai'
$env:OPENAI_API_KEY = '<set locally, never commit>'
$env:OPENAI_MODEL = 'gpt-4.1-mini'
python -m uvicorn app:app --host 127.0.0.1 --port 8766
```

Available environment variables and examples are in `.env.example`. Environment variables are read from the process; `.env` is not loaded automatically.

## Optional n8n handoff

1. Import `n8n/agencyflow-reviewed-webhook.json` into n8n.
2. Inspect the Code and Respond nodes. The supplied workflow does not write to third-party services.
3. Activate the workflow or enter test-listening mode, then copy its **correct** production or test webhook URL.
4. Set `N8N_WEBHOOK_URL` in your AgencyFlow environment and restart the app.
5. Generate a plan, review and edit it, approve it, then press **Send to n8n**. Only the human-approved JSON payload leaves AgencyFlow.

The receiver uses a stable `event_id`. For production, implement durable n8n-side event de-duplication **before** adding external writes. Timeouts can occur after a receiver has accepted an event. The current demo receiver is intentionally side-effect-free.

## Tests

```powershell
python -m pip install pytest httpx
python -m pytest -q
```

Tests cover validation, replay suppression, approval gating, edit lock, authenticated access, failed sends, webhook restrictions and one successful mocked webhook. They **mock the model response**; these are contract and backend tests, not a claim that a live Ollama or hosted LLM run was completed.

## App design

`Browser → FastAPI → Ollama / OpenAI-compatible API → Pydantic validation → SQLite draft → human review/edit → approval → JSON / n8n`

The AI draft is **never automatically approved, exported, emailed, published, or submitted**. A production system would additionally need authentication, rate limiting, strict privacy rules, operational telemetry, a persistent task destination, and receiver-side idempotency.

## Deployment and portfolio presentation

Do not host this app publicly with unprotected, billable AI calls. Require `DEMO_ACCESS_CODE`, apply rate limits, and set secrets in the hosting platform (never in Git). The default process binds to `127.0.0.1`; changing the host and enabling cloud AI is an explicit deployment decision.

For the application, demonstrate one **fictional** restaurant brief, the generated deliverables/tasks, the missing-asset detection, human editing, approval, and the n8n acknowledgment. State precisely what you built and that it is a portfolio project. Do not claim this has been delivered to a paying agency customer.

## Project status

- [x] Functional local web UI and API
- [x] Real LLM adapter (Ollama / compatible cloud API)
- [x] Validation, SQLite deduplication, review and approval
- [x] Optional, safe n8n receiver
- [x] Automated backend tests
- [ ] Verify one end-to-end run with a **live** LLM on the intended machine
- [ ] Import and test the receiver in a real n8n instance
- [ ] Create separate public GitHub repository after confirming no secrets/client data
- [ ] Deploy a protected public demo and record a short walkthrough

**License:** MIT. Fictional sample data only.
