"""Generate n8n/agencyflow-reviewed-webhook.json (run: python n8n/build_workflow.py).

One workflow, four webhooks:
  POST agencyflow-reviewed      approved plan in -> validate, replay check, store task rows locally
  GET  agencyflow-tasks         read stored task rows back (proof for the app)
  POST agencyflow-notion-setup  owner-only: create the sandbox Notion data source, store settings
  POST agencyflow-notion-sync   approved, stored tasks -> Notion (query by external_ref, create or
                                update, read back, record per-task status). Off until set up.
Notion credentials live only in n8n credential storage (id AgFlowNotionCred1).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).with_name("agencyflow-reviewed-webhook.json")
TASKS = "agencyflow_tasks"
SETTINGS = "agencyflow_settings"
TASK_COLS = ["external_ref", "event_id", "campaign", "client", "title", "assignee_role", "priority", "due_date",
             "status", "description", "received_at", "notion_page_id", "notion_url", "notion_status",
             "notion_error", "notion_synced_at"]
# Generic header auth, not n8n's built-in notionApi credential: that one forces Notion-Version 2022-02-22,
# which predates data sources (found by the mock in tests/notion_mock). The secret stays in n8n.
CRED = {"httpHeaderAuth": {"id": "AgFlowNotionCred1", "name": "AgencyFlow Notion (sandbox)"}}
nodes: list[dict] = []
connections: dict = {}


def node(name, typ, ver, pos, params, **extra):
    n = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "agencyflow/" + name)), "name": name,
         "type": typ, "typeVersion": ver, "position": pos, **extra}
    nodes.append(n)
    return name


def link(src, dst, out=0):
    connections.setdefault(src, {"main": []})
    while len(connections[src]["main"]) <= out:
        connections[src]["main"].append([])
    connections[src]["main"][out].append({"node": dst, "type": "main", "index": 0})


def chain(*names):
    for a, b in zip(names, names[1:]):
        link(a, b)


def code(name, pos, js, **extra):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js}, **extra)


def webhook(name, pos, method, path, wid):
    return node(name, "n8n-nodes-base.webhook", 2, pos,
                {"httpMethod": method, "path": path, "responseMode": "responseNode", "options": {}}, webhookId=wid)


def respond(name, pos, body, status="={{ $json.http || 200 }}", **extra):
    return node(name, "n8n-nodes-base.respondToWebhook", 1.1, pos,
                {"respondWith": "json", "responseBody": body, "options": {"responseCode": status}}, **extra)


def ensure_table(name, pos, table, cols):
    return node(name, "n8n-nodes-base.dataTable", 1.1, pos, {
        "resource": "table", "operation": "create", "tableName": table,
        "columns": {"column": [{"name": c, "type": "string"} for c in cols]},
        "options": {"createIfNotExists": True}}, executeOnce=True)


def if_ok(name, pos, expr="={{ $json.ok }}"):
    return node(name, "n8n-nodes-base.if", 2.2, pos, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                       "conditions": [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, name)), "leftValue": expr, "rightValue": "",
                                       "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                       "combinator": "and"}, "options": {}})


def notion_http(name, pos, method, url, body=None, retry=True, **extra):
    """HTTP Request with the Notion credential, an explicit API version, pacing and retries.
    Errors become items (onError: continueRegularOutput) so one failed task never stops the batch."""
    params = {"method": method, "url": url, "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
              "sendHeaders": True,
              "headerParameters": {"parameters": [{"name": "Notion-Version", "value": "={{ $json.settings.notion_version }}"}]},
              "options": {"batching": {"batch": {"batchSize": 1, "batchInterval": 350}}, "timeout": 20000}}
    if body is not None:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
    # Reads retry automatically. Writes do NOT: if Notion created a page but the response was lost, an
    # automatic retry would create a duplicate. A failed write is instead re-checked by external_ref on
    # the next sync, which updates the page if it exists.
    retry_opts = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 1500} if retry else {"retryOnFail": False}
    return node(name, "n8n-nodes-base.httpRequest", 4.2, pos, params, credentials=CRED, onError="continueRegularOutput", **retry_opts, **extra)


# Shared JS helpers (inlined into Code nodes).
ERR_JS = r"""
function describeError(j) {
  const e = j && j.error; if (!e) return null;
  const raw = typeof e === 'string' ? e : [e.message, e.description].filter(Boolean).join(' ');
  const status = Number((typeof e === 'object' && (e.httpCode || e.status)) || (raw.match(/\b(4\d\d|5\d\d)\b/) || [])[1]) || null;
  // Notion's error JSON often arrives escaped inside n8n's message (\"code\":\"rate_limited\").
  const code = (raw.match(/\\?"code\\?"\s*:\s*\\?"([a-z_]+)/) || [])[1] || null;
  const notionMsg = (raw.match(/\\?"message\\?"\s*:\s*\\?"([^"\\]+)/) || [])[1] || null;
  const friendly = {
    unauthorized: 'Notion rejected the credential. Reconnect the AgencyFlow Notion integration in n8n.',
    restricted_resource: 'The Notion integration lacks permission. Share the AgencyFlow data source with it.',
    object_not_found: 'Notion cannot see the data source or page. Share it with the integration.',
    rate_limited: 'Notion rate limit reached after retries. Wait a minute and retry the failed tasks.',
    validation_error: 'Notion rejected a field value (validation error).',
  }[code] || (status === 429 ? 'Notion rate limit reached after retries.' : status >= 500 ? 'Notion had a server error. Retry the failed tasks.' : null);
  return { status, code, message: (friendly || notionMsg || raw || 'Unknown Notion error').slice(0, 300) };
}
"""

# ---------------------------------------------------------------- receiver (unchanged behaviour)
recv = webhook("Receive approved plan", [0, 300], "POST", "agencyflow-reviewed", "d8f42360-f1c5-46e9-9f93-084a37d5329b")
check = code("Validate + replay check", [220, 300], r"""
// Validate the human-approved payload and block replays by event_id (workflow static data).
const body = $input.first().json.body ?? {};
const errors = [];
if (body.schema_version !== 'agencyflow.v1') errors.push('unsupported schema_version');
if (typeof body.event_id !== 'string' || !/^af-\d+-[0-9a-f]{12}$/.test(body.event_id)) errors.push('missing or malformed event_id');
if (!body.reviewed_at) errors.push('plan was not human-approved (reviewed_at missing)');
const tasks = body.plan?.tasks;
if (!Array.isArray(tasks) || tasks.length === 0) errors.push('plan has no tasks');
if (errors.length) return [{ json: { ok: false, http: 422, response: { status: 'rejected', error: errors.join('; ') } } }];
const text = JSON.stringify(body.plan);
let h = 5381; for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
const fingerprint = h.toString(16);
const store = $getWorkflowStaticData('global'); store.seen = store.seen ?? {};
const prev = store.seen[body.event_id];
if (prev && prev.fingerprint === fingerprint)
  return [{ json: { ok: false, http: 200, response: { status: 'duplicate_ignored', duplicate: true, event_id: body.event_id, first_received_at: prev.at, external_actions: 'none' } } }];
if (prev) return [{ json: { ok: false, http: 409, response: { status: 'conflict', error: 'event_id already used for a different plan', event_id: body.event_id } } }];
store.seen[body.event_id] = { fingerprint, at: new Date().toISOString(), tasks: tasks.length };
const ids = Object.keys(store.seen); if (ids.length > 500) for (const id of ids.slice(0, ids.length - 500)) delete store.seen[id];
return [{ json: { ok: true, event: body } }];""")
isnew = if_ok("New approved plan?", [440, 300])
rej = respond("Respond duplicate / rejected", [660, 440], "={{ JSON.stringify($json.response) }}")
ens = ensure_table("Ensure task table exists", [660, 200], TASKS, TASK_COLS)
prep = code("Prepare tracker cards", [880, 200], r"""
// One item per approved task. external_ref is stable, so storing it twice is impossible (upsert below).
const e = $('New approved plan?').first().json.event;
const at = new Date().toISOString();
return e.plan.tasks.map((t, i) => ({ json: {
  external_ref: `${e.event_id}#${i + 1}`, event_id: e.event_id, campaign: e.plan.campaign_name, client: e.client_name ?? '',
  title: t.title, assignee_role: t.role, priority: t.priority, due_date: t.due_date ?? '',
  status: 'To do', description: t.detail ?? '', received_at: at,
} }));""")
store = node("Save tasks (n8n data table, upsert)", "n8n-nodes-base.dataTable", 1.1, [1100, 200], {
    "resource": "row", "operation": "upsert", "dataTableId": {"__rl": True, "mode": "name", "value": TASKS},
    "matchType": "allConditions",
    "filters": {"conditions": [{"keyName": "external_ref", "condition": "eq", "keyValue": "={{ $json.external_ref }}"}]},
    "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": []}, "options": {}})
ack = respond("Acknowledge accepted", [1320, 200], (
    "={{ JSON.stringify({ status: 'accepted', duplicate: false, event_id: $('New approved plan?').first().json.event.event_id, "
    "campaign: $('New approved plan?').first().json.event.plan.campaign_name, cards_prepared: $('Prepare tracker cards').all().length, "
    "tasks_stored: $('Save tasks (n8n data table, upsert)').all().length, stored_in: 'n8n data table " + TASKS + "', "
    "external_actions: 'none (Notion sync is a separate, opt-in step)' }) }}"), status=200, executeOnce=True)
chain(recv, check, isnew); link(isnew, ens, 0); link(isnew, rej, 1); chain(ens, prep, store, ack)

# ---------------------------------------------------------------- read-back
g = webhook("List stored tasks (GET)", [0, 620], "GET", "agencyflow-tasks", "8b0d1c52-6a3e-4f3b-9d0e-5f1a2b3c4d5e")
gens = ensure_table("Ensure task table exists (read)", [220, 620], TASKS, TASK_COLS)
gread = node("Read stored tasks", "n8n-nodes-base.dataTable", 1.1, [440, 620], {
    "resource": "row", "operation": "get", "dataTableId": {"__rl": True, "mode": "name", "value": TASKS},
    "returnAll": True, "options": {}}, alwaysOutputData=True)
greply = respond("Return stored tasks", [660, 620],
                 "={{ JSON.stringify({ tasks: $input.all().map(i => i.json).filter(r => r.external_ref) }) }}", status=200, executeOnce=True)
chain(g, gens, gread, greply)

# ---------------------------------------------------------------- Notion setup (owner-only)
SETTINGS_JS = r"""
const ALLOWED = ['https://api.notion.com/v1', 'http://notion-mock:8787/v1'];  // never send the token anywhere else
function settingsFrom(rows) {
  const s = Object.fromEntries(rows.filter(r => r.key).map(r => [r.key, r.value]));
  return { notion_enabled: s.notion_enabled === 'true', notion_data_source_id: s.notion_data_source_id || '',
           notion_api_base: s.notion_api_base || 'https://api.notion.com/v1', notion_version: s.notion_version || '2025-09-03' };
}
"""
s0 = webhook("Notion setup (owner)", [0, 960], "POST", "agencyflow-notion-setup", "3c1f9a10-2b7e-4d55-8e21-6a9b0c4d7e10")
s1 = ensure_table("Ensure settings table", [220, 960], SETTINGS, ["key", "value"])
s2 = code("Check setup request", [440, 960], SETTINGS_JS + r"""
const b = $('Notion setup (owner)').first().json.body ?? {};
const api = b.api_base || 'https://api.notion.com/v1';
const version = b.notion_version || '2025-09-03';
const fail = (msg) => [{ json: { ok: false, http: 422, response: { status: 'rejected', error: msg } } }];
if (!ALLOWED.includes(api)) return fail('api_base is not an allowed Notion endpoint');
const norm = (id) => String(id || '').replace(/-/g, '');
if (b.data_source_id && !/^[0-9a-f]{32}$/i.test(norm(b.data_source_id))) return fail('data_source_id must be a Notion id');
if (!b.data_source_id && !/^[0-9a-f]{32}$/i.test(norm(b.parent_page_id))) return fail('parent_page_id (a page shared with the integration) or data_source_id is required');
return [{ json: { ok: true, create: !b.data_source_id, parent_page_id: b.parent_page_id, data_source_id: b.data_source_id || '',
                  enable: b.enable === true, settings: { notion_api_base: api, notion_version: version } } }];""")
s3 = if_ok("Setup request valid?", [660, 960])
s3r = respond("Respond setup rejected", [880, 1100], "={{ JSON.stringify($json.response) }}")
s4 = if_ok("Create a new data source?", [880, 900], "={{ $json.create }}")
s5 = notion_http("Create sandbox database", [1100, 840], "POST", "={{ $json.settings.notion_api_base }}/databases", (
    "={{ JSON.stringify({ parent: { type: 'page_id', page_id: $json.parent_page_id }, "
    "title: [{ type: 'text', text: { content: 'AgencyFlow AI tasks (fictional demo)' } }], "
    "initial_data_source: { properties: { "
    "'Name': { title: {} }, 'External ref': { rich_text: {} }, 'Event ID': { rich_text: {} }, 'Campaign': { rich_text: {} }, "
    "'Client': { rich_text: {} }, 'Role': { rich_text: {} }, "
    "'Priority': { select: { options: [{ name: 'high', color: 'red' }, { name: 'medium', color: 'yellow' }, { name: 'low', color: 'gray' }] } }, "
    "'Due': { date: {} }, 'Description': { rich_text: {} }, 'Source': { rich_text: {} }, "
    "'Status': { select: { options: [{ name: 'To do', color: 'default' }, { name: 'In progress', color: 'blue' }, { name: 'Done', color: 'green' }] } } "
    "} } }) }}"), retry=False)
s6 = notion_http("Look up data source id", [1320, 840], "GET",
                 "={{ $('Check setup request').first().json.settings.notion_api_base }}/databases/{{ $json.id }}")
nodes[-1]["parameters"]["headerParameters"]["parameters"][0]["value"] = "={{ $('Check setup request').first().json.settings.notion_version }}"
s7 = code("Settings to store", [1540, 960], ERR_JS + r"""
const req = $('Check setup request').first().json;
let dsId = req.data_source_id;
if (req.create) {
  const looked = $('Look up data source id').first().json;
  const err = describeError($('Create sandbox database').first().json) || describeError(looked);
  if (err) return [{ json: { ok: false, http: 502, response: { status: 'setup_failed', error: err.message } } }];
  dsId = (looked.data_sources || [])[0]?.id || '';
  if (!dsId) return [{ json: { ok: false, http: 502, response: { status: 'setup_failed', error: 'Notion returned no data source id' } } }];
}
const rows = { notion_data_source_id: dsId, notion_api_base: req.settings.notion_api_base,
               notion_version: req.settings.notion_version, notion_enabled: String(req.enable) };
return Object.entries(rows).map(([key, value]) => ({ json: { ok: true, key, value } }));""")
link(s4, s5, 0); link(s4, s7, 1); chain(s5, s6, s7)
s8 = if_ok("Setup succeeded?", [1760, 960], "={{ $input.first().json.ok }}")
s9 = node("Store settings", "n8n-nodes-base.dataTable", 1.1, [1980, 900], {
    "resource": "row", "operation": "upsert", "dataTableId": {"__rl": True, "mode": "name", "value": SETTINGS},
    "matchType": "allConditions",
    "filters": {"conditions": [{"keyName": "key", "condition": "eq", "keyValue": "={{ $json.key }}"}]},
    "columns": {"mappingMode": "defineBelow", "value": {"key": "={{ $json.key }}", "value": "={{ $json.value }}"},
                "matchingColumns": [], "schema": [{"id": "key", "displayName": "key", "type": "string", "display": True, "removed": False},
                                                  {"id": "value", "displayName": "value", "type": "string", "display": True, "removed": False}]},
    "options": {}})
s10 = respond("Respond setup done", [2200, 900], (
    "={{ JSON.stringify({ status: 'configured', data_source_id: $('Settings to store').all().find(i => i.json.key === 'notion_data_source_id').json.value, "
    "enabled: $('Settings to store').all().find(i => i.json.key === 'notion_enabled').json.value === 'true' }) }}"), status=200, executeOnce=True)
s11 = respond("Respond setup failed", [1980, 1040], "={{ JSON.stringify($json.response) }}")
chain(s0, s1, s2, s3); link(s3, s4, 0); link(s3, s3r, 1); link(s7, s8); link(s8, s9, 0); link(s8, s11, 1); chain(s9, s10)

# ---------------------------------------------------------------- Notion sync
n0 = webhook("Notion sync request", [0, 1400], "POST", "agencyflow-notion-sync", "5e7a2c44-9d1b-4f60-b3a8-7c2e1f0d9a21")
n1 = ensure_table("Ensure settings table (sync)", [220, 1400], SETTINGS, ["key", "value"])
n2 = node("Read settings", "n8n-nodes-base.dataTable", 1.1, [440, 1400], {
    "resource": "row", "operation": "get", "dataTableId": {"__rl": True, "mode": "name", "value": SETTINGS},
    "returnAll": True, "options": {}}, alwaysOutputData=True, executeOnce=True)
n3 = code("Check sync request", [660, 1400], SETTINGS_JS + r"""
const b = $('Notion sync request').first().json.body ?? {};
const s = settingsFrom($input.all().map(i => i.json));
const no = (http, status, error) => [{ json: { ok: false, http, response: { status, error } } }];
if (typeof b.event_id !== 'string' || !/^af-\d+-[0-9a-f]{12}$/.test(b.event_id)) return no(422, 'rejected', 'missing or malformed event_id');
if (!s.notion_data_source_id) return no(409, 'not_configured', 'Notion sync is not set up. The owner runs scripts/notion-setup.ps1 first.');
if (!s.notion_enabled) return no(409, 'disabled', 'Notion sync is configured but disabled (external writes are off by default).');
if (!ALLOWED.includes(s.notion_api_base)) return no(409, 'not_configured', 'Configured Notion endpoint is not allowed.');
return [{ json: { ok: true, event_id: b.event_id, settings: s } }];""")
n4 = if_ok("Sync allowed?", [880, 1400])
n4r = respond("Respond sync refused", [1100, 1540], "={{ JSON.stringify($json.response) }}")
n5 = ensure_table("Ensure task table exists (sync)", [1100, 1340], TASKS, TASK_COLS)
n6 = node("Read approved task rows", "n8n-nodes-base.dataTable", 1.1, [1320, 1340], {
    "resource": "row", "operation": "get", "dataTableId": {"__rl": True, "mode": "name", "value": TASKS},
    "matchType": "allConditions",
    "filters": {"conditions": [{"keyName": "event_id", "condition": "eq", "keyValue": "={{ $('Check sync request').first().json.event_id }}"}]},
    "returnAll": True, "options": {}}, alwaysOutputData=True, executeOnce=True)
n7 = code("Build Notion pages", [1540, 1340], r"""
// Only tasks that passed the approval receiver and were stored locally can reach Notion.
const req = $('Check sync request').first().json;
const rows = $input.all().map(i => i.json).filter(r => r.external_ref && r.event_id === req.event_id);
if (!rows.length) return [{ json: { ok: false, http: 404, response: { status: 'not_found', error: 'No approved, stored tasks for this event_id. Send the plan to n8n first.' } } }];
const rt = (s) => ({ rich_text: [{ type: 'text', text: { content: String(s ?? '').slice(0, 1900) } }] });
return rows.map(r => ({ json: { ok: true, settings: req.settings, external_ref: r.external_ref, row: r, properties: {
  'Name': { title: [{ type: 'text', text: { content: String(r.title).slice(0, 1900) } }] },
  'External ref': rt(r.external_ref), 'Event ID': rt(r.event_id), 'Campaign': rt(r.campaign), 'Client': rt(r.client),
  'Role': rt(r.assignee_role), 'Priority': { select: { name: r.priority } },
  'Due': { date: r.due_date ? { start: r.due_date } : null }, 'Description': rt(r.description),
  'Source': rt('AgencyFlow AI (independent fictional demo)'),
} } }));""")
n8 = if_ok("Any tasks?", [1760, 1340], "={{ $input.first().json.ok }}")
n8r = respond("Respond no tasks", [1980, 1480], "={{ JSON.stringify($json.response) }}", executeOnce=True)
n9 = notion_http("Find page by external_ref", [1980, 1280], "POST",
                 "={{ $json.settings.notion_api_base }}/data_sources/{{ $json.settings.notion_data_source_id }}/query",
                 "={{ JSON.stringify({ filter: { property: 'External ref', rich_text: { equals: $json.external_ref } }, page_size: 5 }) }}")
n10 = code("Create, update or hold", [2200, 1280], ERR_JS + r"""
// Never create when we could not confirm the page is absent: that is how duplicates happen.
const built = $('Build Notion pages').all();
return $input.all().map((it, i) => {
  const b = built[i].json, err = describeError(it.json);
  if (err) return { json: { ...b, action: 'hold', find_error: err } };
  const found = it.json.results || [];
  if (found.length) return { json: { ...b, action: 'update', page_id: found[0].id, duplicates_seen: found.length } };
  return { json: { ...b, action: 'create' } };
});""")
n11 = notion_http("Write page (create / update)", [2420, 1280],
                  "={{ $json.action === 'update' ? 'PATCH' : 'POST' }}",
                  "={{ $json.settings.notion_api_base }}/{{ $json.action === 'create' ? 'pages' : $json.action === 'update' ? 'pages/' + $json.page_id : 'data_sources/' + $json.settings.notion_data_source_id + '/query' }}",
                  ("={{ JSON.stringify($json.action === 'hold' ? { filter: { property: 'External ref', rich_text: { equals: '__agencyflow_hold__' } }, page_size: 1 } : $json.action === 'create' "
                   "? { parent: { type: 'data_source_id', data_source_id: $json.settings.notion_data_source_id }, "
                   "properties: Object.assign({}, $json.properties, { 'Status': { select: { name: 'To do' } } }) } "
                   ": { properties: $json.properties }) }}"), retry=False)
# 'hold' items make a harmless read (an empty query) so the batch keeps its order; nothing is written.
n12 = notion_http("Read back pages for this plan", [2640, 1280], "POST",
                  "={{ $('Check sync request').first().json.settings.notion_api_base }}/data_sources/{{ $('Check sync request').first().json.settings.notion_data_source_id }}/query",
                  "={{ JSON.stringify({ filter: { property: 'Event ID', rich_text: { equals: $('Check sync request').first().json.event_id } }, page_size: 100 }) }}",
                  executeOnce=True, alwaysOutputData=True)
nodes[-1]["parameters"]["headerParameters"]["parameters"][0]["value"] = "={{ $('Check sync request').first().json.settings.notion_version }}"
n13 = code("Verify and reconcile", [2860, 1280], ERR_JS + r"""
// Success is only what Notion reads back: right page, right fields. Everything else is reported.
const plan = $('Create, update or hold').all().map(i => i.json);
const writes = $('Write page (create / update)').all().map(i => i.json);
const back = $input.first().json, backErr = describeError(back);
const txt = (p) => ((p && (p.title || p.rich_text)) || []).map(t => t.plain_text ?? t.text?.content ?? '').join('');
const pages = backErr ? [] : (back.results || []);
const now = new Date().toISOString();
return plan.map((t, i) => {
  const err = t.action === 'hold' ? t.find_error : describeError(writes[i]);
  const mine = pages.filter(p => txt(p.properties['External ref']) === t.external_ref);
  const p = mine[0];
  let status, error = '';
  if (backErr) { status = 'unverified'; error = 'Could not read Notion back: ' + backErr.message; }
  else if (!p) { status = 'failed'; error = err ? err.message : 'Page not found in Notion after writing.'; }
  else {
    const want = t.row, got = p.properties;
    const diffs = [];
    if (txt(got['Name']) !== String(want.title).slice(0, 1900)) diffs.push('title');
    if ((got['Priority']?.select?.name || '') !== want.priority) diffs.push('priority');
    if ((got['Due']?.date?.start || '') !== (want.due_date || '')) diffs.push('due date');
    if (txt(got['Role']) !== String(want.assignee_role)) diffs.push('role');
    if (txt(got['Campaign']) !== String(want.campaign)) diffs.push('campaign');
    if (diffs.length) { status = 'mismatch'; error = 'Notion shows different ' + diffs.join(', ') + (err ? ' (' + err.message + ')' : ''); }
    else status = mine.length > 1 ? 'synced_duplicates' : 'synced';
    if (mine.length > 1) error = `${mine.length} Notion pages share this external_ref (created outside AgencyFlow?).`;
  }
  return { json: { external_ref: t.external_ref, notion_page_id: p?.id || t.row.notion_page_id || '', notion_url: p?.url || t.row.notion_url || '',
                   notion_status: status, notion_error: error, notion_synced_at: now, _action: t.action, _title: t.row.title } };
});""")
n14 = node("Record sync status", "n8n-nodes-base.dataTable", 1.1, [3080, 1280], {
    "resource": "row", "operation": "upsert", "dataTableId": {"__rl": True, "mode": "name", "value": TASKS},
    "matchType": "allConditions",
    "filters": {"conditions": [{"keyName": "external_ref", "condition": "eq", "keyValue": "={{ $json.external_ref }}"}]},
    "columns": {"mappingMode": "defineBelow", "value": {c: "={{ $json." + c + " }}" for c in
                ["external_ref", "notion_page_id", "notion_url", "notion_status", "notion_error", "notion_synced_at"]},
                "matchingColumns": [], "schema": [{"id": c, "displayName": c, "type": "string", "display": True, "removed": False} for c in
                ["external_ref", "notion_page_id", "notion_url", "notion_status", "notion_error", "notion_synced_at"]]},
    "options": {}})
n15 = respond("Respond sync result", [3300, 1280], (
    "={{ (() => { const t = $('Verify and reconcile').all().map(i => i.json); "
    "const ok = t.filter(x => x.notion_status.startsWith('synced')).length; "
    "return JSON.stringify({ status: ok === t.length ? 'complete' : ok ? 'partial' : 'failed', "
    "event_id: $('Check sync request').first().json.event_id, synced: ok, total: t.length, "
    "tasks: t.map(x => ({ external_ref: x.external_ref, title: x._title, action: x._action, status: x.notion_status, url: x.notion_url, error: x.notion_error })) }); })() }}"),
    status=200, executeOnce=True)
chain(n0, n1, n2, n3, n4); link(n4, n5, 0); link(n4, n4r, 1); chain(n5, n6, n7, n8)
link(n8, n9, 0); link(n8, n8r, 1); chain(n9, n10, n11, n12, n13, n14, n15)

wf = {"id": "AgFlowReceiverV2",
      "name": "AgencyFlow AI - Approved plan receiver, local task store, opt-in Notion sync",
      "nodes": nodes, "connections": connections, "active": False, "settings": {"executionOrder": "v1"},
      "pinData": {}, "tags": []}
OUT.write_text(json.dumps(wf, indent=2), encoding="utf-8")
print(f"wrote {OUT.name}: {len(nodes)} nodes")
