// Optional campaign discovery panel. It never infers client facts or sends messages.
// Loaded after app.js; receives its existing API client and current run from render().
(() => {
  'use strict';
  const fields = [
    ['business_goal', 'Business objective', 'What should change for the restaurant?'],
    ['target_audience', 'Target audience', 'Which customers and location matter?'],
    ['primary_kpi', 'Primary success metric', 'How would success be measured?'],
    ['measurement_source', 'Measurement source', 'Which source reports that metric?'],
    ['baseline', 'Verified baseline', 'Current value and reporting period'],
    ['target', 'Agreed target', 'Target, units and reporting period'],
    ['budget', 'Budget', 'Approved creative or media budget'],
    ['platforms', 'Channels', 'Confirmed channels and placements'],
  ];
  const escape = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
  const drafts = new Map();
  let runId = null;
  let apiClient = null;

  function statusText(s) {
    return {unknown: 'Needs confirmation', proposed: 'Working suggestion',
      client_confirmed: 'Client-confirmed'}[s] || s;
  }

  function markup(data, editable, questions) {
    const inputs = fields.map(([key, title, hint]) => {
      const f = data[key] || {status: 'unknown', value: null, provenance: null};
      const row = `<div class="context-field" data-context-row="${key}">
        <label for="context-value-${key}">${escape(title)}</label>
        <p class="context-hint">${escape(hint)}</p>
        <div class="context-inputs">
          <select data-context-status="${key}" aria-label="${escape(title)} verification status" ${editable ? '' : 'disabled'}>
            ${[['unknown', 'Not confirmed'], ['proposed', 'Proposed only'], ['client_confirmed', 'Confirmed by client']]
              .map(([v, name]) => `<option value="${v}" ${f.status === v ? 'selected' : ''}>${name}</option>`).join('')}
          </select>
          <input id="context-value-${key}" data-context-value="${key}" maxlength="500"
            value="${escape(f.value || '')}" placeholder="${escape(hint)}"
            ${editable && f.status !== 'unknown' ? '' : 'disabled'}>
        </div>
        <input data-context-provenance="${key}" class="context-source"
          maxlength="350" value="${escape(f.provenance || '')}"
          placeholder="Source (e.g. confirmed in client call on date)"
          aria-label="${escape(title)} source"
          ${editable && f.status === 'client_confirmed' ? '' : 'disabled'}>
      </div>`;
      return row;
    });
    // Keep the first four strategic questions prominent.
    const compact = inputs.slice(0, 4).join('');
    const extras = inputs.slice(4).join('');
    const missing = questions.length
      ? `<div class="context-questions"><strong>Questions still needing confirmation</strong><ul>
        ${questions.map(q => `<li>${escape(q)}</li>`).join('')}</ul>
        <p>Suggested questions only; the app does not contact the client.</p></div>`
      : `<p class="context-complete">All discovery fields marked client-confirmed.</p>`;
    return `<div class="context-head"><span class="step-tag">CLIENT DISCOVERY / OPTIONAL</span>
      <h3>What is this campaign trying to achieve?</h3>
      <p>Separate confirmed client facts from suggested strategy. Unknowns stay unknown until someone verifies them.</p></div>
      <form id="campaignContextForm">
        <div class="context-grid">${compact}</div>
        <details class="context-more"><summary>Budget, channels, baseline and target</summary>
          <div class="context-grid">${extras}</div></details>
        <div class="context-actions">
          ${editable ? '<button class="button button-outline" type="submit" id="saveContextBtn">Save campaign context</button>' :
            '<span class="context-readonly">Locked after plan approval</span>'}
          <span id="contextSaveStatus" role="status"></span>
        </div>
        <div id="contextQuestions">${missing}</div>
      </form>`;
  }

  function collect(root) {
    const context = {};
    for (const [key] of fields) {
      const status = root.querySelector(`[data-context-status="${key}"]`).value;
      const value = root.querySelector(`[data-context-value="${key}"]`).value.trim();
      const provenance = root.querySelector(`[data-context-provenance="${key}"]`).value.trim();
      if (status === 'unknown') {
        context[key] = {status, value: null, provenance: null};
      } else {
        if (!value) throw Error(`Enter a value for ${key.replaceAll('_', ' ')} or mark it unknown.`);
        if (status === 'client_confirmed' && !provenance) {
          throw Error(`Add where the client confirmed ${key.replaceAll('_', ' ')}.`);
        }
        context[key] = {status, value, provenance: provenance || null};
      }
    }
    return context;
  }

  function updateInputStates(root) {
    for (const [key] of fields) {
      const s = root.querySelector(`[data-context-status="${key}"]`).value;
      const value = root.querySelector(`[data-context-value="${key}"]`);
      const source = root.querySelector(`[data-context-provenance="${key}"]`);
      value.disabled = s === 'unknown';
      source.disabled = s !== 'client_confirmed';
    }
  }

  async function mount(run, api) {
    runId = run?.id ?? null;
    apiClient = api;
    const area = document.getElementById('planArea');
    if (!run || !area) return;
    const panel = document.createElement('section');
    panel.id = 'campaignContextPanel';
    panel.className = 'campaign-context';
    panel.innerHTML = '<p class="hint">Loading campaign context…</p>';
    const actions = area.querySelector('.plan-actions');
    if (actions) actions.before(panel); else area.append(panel);
    try {
      const resp = await api(`/api/runs/${run.id}/context`);
      if (runId !== run.id || !panel.isConnected) return;
      const pending = drafts.get(run.id);
      const data = pending?.data || resp.context;
      panel.innerHTML = markup(data, run.status === 'review', resp.questions);
      const form = panel.querySelector('form');
      if (run.status !== 'review') return;

      const capture = () => {
        // Preserve even incomplete edits if another plan widget re-renders.
        // Validation happens on save, not while a person is still typing.
        const snapshot = {};
        for (const [key] of fields) {
          const status = form.querySelector(`[data-context-status="${key}"]`).value;
          snapshot[key] = status === 'unknown'
            ? {status, value: null, provenance: null}
            : {status,
               value: form.querySelector(`[data-context-value="${key}"]`).value,
               provenance: form.querySelector(`[data-context-provenance="${key}"]`).value};
        }
        drafts.set(run.id, {data: snapshot, dirty: true});
      };
      form.addEventListener('input', capture);
      form.addEventListener('change', (e) => {
        if (e.target.matches('[data-context-status]')) updateInputStates(form);
        capture();
      });
      form.addEventListener('submit', async e => {
        e.preventDefault();
        if (await savePending()) {
          const status = document.getElementById('contextSaveStatus');
          if (status) status.textContent = 'Saved · no external actions';
        }
      });
    } catch (err) {
      if (runId === run.id && panel.isConnected) {
        panel.textContent = 'Campaign context unavailable: ' + err.message;
      }
    }
  }

  async function savePending() {
    const pending = drafts.get(runId);
    if (!pending?.dirty) return true;
    const root = document.getElementById('campaignContextPanel');
    const form = root?.querySelector('form');
    if (!form || !apiClient) return false;
    const status = document.getElementById('contextSaveStatus');
    try {
      const data = collect(form);
      const idAtSave = runId;
      const response = await apiClient(`/api/runs/${idAtSave}/context`, {
        method: 'PUT', body: JSON.stringify(data),
      });
      if (runId !== idAtSave) return false;
      drafts.delete(idAtSave);
      const questions = root.querySelector('#contextQuestions');
      if (questions) {
        questions.innerHTML = response.questions.length
          ? `<div class="context-questions"><strong>Questions still needing confirmation</strong><ul>
            ${response.questions.map(q => `<li>${escape(q)}</li>`).join('')}</ul>
            <p>Suggested questions only; no client messages are sent.</p></div>`
          : '<p class="context-complete">All discovery fields marked client-confirmed.</p>';
      }
      if (status) status.textContent = response.changed ? 'Saved · no external actions' : 'Already saved';
      if (typeof loadActivity === 'function') loadActivity();
      return true;
    } catch (err) {
      if (status) status.textContent = err.message;
      return false;
    }
  }

  window.AgencyFlowCampaignContext = {
    mount,
    savePending,
    isDirty: () => Boolean(drafts.get(runId)?.dirty),
    discardPending: () => { if (runId != null) drafts.delete(runId); },
  };
})();
