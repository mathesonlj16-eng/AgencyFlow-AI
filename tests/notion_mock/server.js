// Minimal Notion API stand-in for integration tests (no real account, no network).
// Implements only what AgencyFlow uses, with the 2025-09-03 data-source model, and
// lets a test inject failures: rate limits, permission errors and server errors.
const http = require('http');
const { randomUUID } = require('crypto');

const VERSION = '2025-09-03';
let pages = new Map();          // id -> page
let dataSources = new Map();    // id -> { schema, database_id }
let failures = [];              // { op, status, code, remaining, skip }
let calls = [];

const json = (res, status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
const notionError = (res, status, code, message) => json(res, status, { object: 'error', status, code, message });
const text = (prop) => (prop?.rich_text || prop?.title || []).map(t => t.text?.content ?? t.plain_text ?? '').join('');

function maybeFail(op, res) {
  const f = failures.find(x => x.op === op && x.remaining > 0);
  if (!f) return false;
  if (f.skip > 0) { f.skip--; return false; }
  f.remaining--;
  const headers = f.status === 429 ? { 'Retry-After': '1' } : {};
  res.writeHead(f.status, { 'Content-Type': 'application/json', ...headers });
  res.end(JSON.stringify({ object: 'error', status: f.status, code: f.code, message: f.message || `injected ${f.code}` }));
  return true;
}

function matches(page, filter) {
  if (!filter) return true;
  if (filter.and) return filter.and.every(f => matches(page, f));
  const prop = page.properties[filter.property];
  if (filter.rich_text?.equals !== undefined) return text(prop) === filter.rich_text.equals;
  return false;
}

function publicPage(p) {
  return { object: 'page', id: p.id, url: `https://www.notion.so/mock-${p.id.replace(/-/g, '')}`,
           in_trash: false, parent: { type: 'data_source_id', data_source_id: p.data_source_id },
           properties: p.properties, last_edited_time: p.edited };
}

// Echo stored values back in Notion's response shape so read-back verification is realistic.
function normalize(props) {
  const out = {};
  for (const [k, v] of Object.entries(props || {})) {
    if (v.title) out[k] = { type: 'title', title: v.title.map(t => ({ type: 'text', text: t.text, plain_text: t.text.content })) };
    else if (v.rich_text) out[k] = { type: 'rich_text', rich_text: v.rich_text.map(t => ({ type: 'text', text: t.text, plain_text: t.text.content })) };
    else if (v.select !== undefined) out[k] = { type: 'select', select: v.select };
    else if (v.date !== undefined) out[k] = { type: 'date', date: v.date };
    else out[k] = v;
  }
  return out;
}

http.createServer((req, res) => {
  let body = '';
  req.on('data', c => { body += c; });
  req.on('end', () => {
    const data = body ? JSON.parse(body) : {};
    const url = new URL(req.url, 'http://mock');
    const path = url.pathname;
    // ---- test control endpoints ----
    if (path === '/__reset') { pages = new Map(); failures = []; calls = []; return json(res, 200, { ok: true }); }
    if (path === '/__clear_failures') { failures = []; return json(res, 200, { ok: true }); }
    if (path === '/__fail') { failures.push({ remaining: 1, skip: 0, code: 'internal_server_error', ...data }); return json(res, 200, { failures }); }
    if (path === '/__state') return json(res, 200, { pages: [...pages.values()].map(publicPage), calls, dataSources: [...dataSources.keys()] });
    if (path === '/__edit') { const p = [...pages.values()].find(x => text(x.properties['External ref']) === data.external_ref); if (p) Object.assign(p.properties, normalize(data.properties)); return json(res, p ? 200 : 404, { ok: !!p }); }
    // ---- Notion API surface ----
    calls.push(`${req.method} ${path}`);
    const auth = (req.headers.authorization || '').trim();
    if (!/^Bearer \S+/.test(auth) || auth === 'Bearer not-configured') return notionError(res, 401, 'unauthorized', 'API token is invalid.');
    if (req.headers['notion-version'] !== VERSION) return notionError(res, 400, 'invalid_request', `Mock requires Notion-Version ${VERSION}, got ${req.headers['notion-version']}`);
    let m;
    if (req.method === 'POST' && path === '/v1/databases') {
      if (maybeFail('setup', res)) return;
      const dbId = randomUUID(), dsId = randomUUID();
      dataSources.set(dsId, { database_id: dbId, schema: data.initial_data_source?.properties || {} });
      return json(res, 200, { object: 'database', id: dbId, data_sources: [{ id: dsId, name: 'AgencyFlow tasks' }] });
    }
    if (req.method === 'GET' && (m = path.match(/^\/v1\/databases\/([^/]+)$/))) {
      const ds = [...dataSources.entries()].find(([, v]) => v.database_id === m[1]);
      if (!ds) return notionError(res, 404, 'object_not_found', 'Could not find database.');
      return json(res, 200, { object: 'database', id: m[1], data_sources: [{ id: ds[0], name: 'AgencyFlow tasks' }] });
    }
    if (req.method === 'GET' && path === '/v1/users/me') return json(res, 200, { object: 'user', type: 'bot', name: 'AgencyFlow mock bot' });
    if (req.method === 'POST' && (m = path.match(/^\/v1\/data_sources\/([^/]+)\/query$/))) {
      if (!dataSources.has(m[1])) return notionError(res, 404, 'object_not_found', 'Could not find data source. Make sure it is shared with the integration.');
      if (maybeFail('query', res)) return;
      const results = [...pages.values()].filter(p => p.data_source_id === m[1] && matches(p, data.filter)).map(publicPage);
      return json(res, 200, { object: 'list', results, has_more: false, next_cursor: null });
    }
    if (req.method === 'POST' && path === '/v1/pages') {
      const ds = data.parent?.data_source_id;
      if (data.parent?.type !== 'data_source_id' || !dataSources.has(ds)) {
        calls.push('rejected body: ' + body.slice(0, 300));
        return notionError(res, 400, 'validation_error', 'parent.data_source_id is required (legacy database_id parent is not accepted here).');
      }
      const lost = failures.find(x => x.op === 'create_lost' && x.remaining > 0);
      if (!lost && maybeFail('create', res)) return;
      const id = randomUUID();
      pages.set(id, { id, data_source_id: ds, properties: normalize(data.properties), edited: new Date().toISOString() });
      if (lost) { lost.remaining--; return notionError(res, 502, 'internal_server_error', 'injected: page created but response lost'); }
      return json(res, 200, publicPage(pages.get(id)));
    }
    if (req.method === 'PATCH' && (m = path.match(/^\/v1\/pages\/([^/]+)$/))) {
      const p = pages.get(m[1]);
      if (!p) return notionError(res, 404, 'object_not_found', 'Could not find page.');
      if (maybeFail('update', res)) return;
      Object.assign(p.properties, normalize(data.properties)); p.edited = new Date().toISOString();
      return json(res, 200, publicPage(p));
    }
    return notionError(res, 404, 'invalid_request_url', `Mock does not implement ${req.method} ${path}`);
  });
}).listen(8787, () => console.log('notion mock on :8787'));
