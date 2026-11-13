const el = (id) => document.getElementById(id);
const escapeHtml = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const samples = {
  restaurant: {client:'Northstar Kitchen (fictional)',brief:'We need three Instagram reels for our winter menu launch next Friday. We have food photos, but no videos yet. Make it warm and modern without looking too Christmas-specific. Also create two story graphics and a caption for each reel. We want a draft for review before anything gets published.'},
  hotel: {client:'Harbour House Hotel (fictional)',brief:'Our boutique hotel is opening a rooftop bar next month. Could your team make six social posts, a short teaser video, and email copy for our guest list? We have brand guidelines but have not confirmed the photo shoot. Please propose a launch schedule and tell us what you need from our staff.'}
};
let current = null; let settings = {}; let savedCode = '';
try { savedCode = sessionStorage.getItem('agencyflow_code') || ''; } catch (_) {}
function message(text){const t=el('toast');t.textContent=text;t.classList.add('show');clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>t.classList.remove('show'),4300);}
async function api(path, options={}){
  const headers = {'Content-Type':'application/json',...(savedCode?{'X-Demo-Code':savedCode}:{})};
  let response;
  try {response = await fetch(path,{...options,headers:{...headers,...options.headers}});} catch(e){throw new Error('Cannot reach AgencyFlow server. Make sure it is running.');}
  let data={}; try{data=await response.json();}catch(_e){}
  if(response.status===401){el('accessDialog').showModal();throw new Error('Access code required.');}
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status})`);
  return data;
}
async function initialize(){
  try{settings=await api('/api/config');el('providerLabel').textContent=`${settings.provider === 'ollama'?'Ollama selected':'Cloud model selected'} · ${settings.model}`;if(settings.access_code_required&&!savedCode)el('accessDialog').showModal();}
  catch(e){el('providerLabel').textContent='Server unavailable';message(e.message);}
}
function textInput(value, field){return `<input class="edit-field ${field === 'campaign_name' ? 'plan-title-field' : ''}" data-field="${field}" value="${escapeHtml(value)}">`}
function render(){
  const empty=el('emptyState');const area=el('planArea');const badge=el('runBadge');
  if(!current){empty.hidden=false;area.hidden=true;badge.textContent='Awaiting brief';badge.className='pill';return;}
  const p=current.plan;const editable=current.status==='review';empty.hidden=true;area.hidden=false;
  badge.textContent=current.status==='review'?'Needs review':current.status==='approved'?'Approved':'Handed off';badge.className=`pill ${current.status}`;
  const options=(priority)=>['high','medium','low'].map(x=>`<option value="${x}" ${priority===x?'selected':''}>${x}</option>`).join('');
  let body=`<div class="notice ${editable?'':'success'}">${editable?'AI-generated draft — verify every detail and edit any task before approving.':'✓ Human-approved plan. This can now be exported'+(settings.handoff_enabled?' or sent to n8n.':'.')}</div>`;
  body+=`<div class="plan-section"><h4>CAMPAIGN</h4>${editable?textInput(p.campaign_name,'campaign_name'):`<h3>${escapeHtml(p.campaign_name)}</h3>`}<div class="summary-text">${editable?`<textarea class="edit-field" data-field="summary" rows="3">${escapeHtml(p.summary)}</textarea>`:escapeHtml(p.summary)}</div><div class="plan-meta"><span>◷ Deadline: ${escapeHtml(p.deadline||'Needs confirmation')}</span><span>◇ ${p.tasks.length} tasks</span><span>◫ ${p.deliverables.length} deliverable types</span></div></div>`;
  body+=`<div class="plan-section"><h4>DELIVERABLES</h4>${p.deliverables.map(d=>`<div class="deliverable"><span>${escapeHtml(d.name)} <span style="color:#a2abb7">/ ${escapeHtml(d.channel)}</span></span><span class="deliverable-count">× ${d.quantity}</span></div>`).join('')}</div>`;
  body+=`<div class="plan-section"><h4>PRODUCTION TASKS</h4>${p.tasks.map((t,i)=>`<div class="task-card"><div class="task-top"><span class="task-index">${String(i+1).padStart(2,'0')}</span>${editable?`<input aria-label="Task ${i+1} title" class="task-edit" data-task="${i}" data-key="title" value="${escapeHtml(t.title)}">`:`<strong style="font-size:12px">${escapeHtml(t.title)}</strong>`}${editable?`<select aria-label="Task priority" data-task="${i}" data-key="priority">${options(t.priority)}</select>`:`<span class="deliverable-count">${escapeHtml(t.priority)}</span>`}</div><div class="task-detail">${escapeHtml(t.role)} · ${escapeHtml(t.detail)}</div></div>`).join('')}</div>`;
  if(p.missing_inputs.length) body+=`<div class="plan-section"><h4>QUESTIONS FOR THE CLIENT</h4>${p.missing_inputs.map(s=>`<div class="warning-note">? ${escapeHtml(s)}</div>`).join('')}</div>`;
  if(p.risks.length) body+=`<div class="plan-section"><h4>RISKS TO REVIEW</h4>${p.risks.map(s=>`<div class="warning-note">! ${escapeHtml(s)}</div>`).join('')}</div>`;
  body+=`<div class="plan-actions">${editable?'<button class="button button-light" id="saveBtn">Save edits</button><button class="button button-primary" id="approveBtn">Approve plan ✓</button>':`<button class="button button-outline" id="exportBtn">Export JSON ↓</button>${current.status==='approved'&&settings.handoff_enabled?'<button class="button button-primary" id="handoffBtn">Send to n8n →</button>':''}`}</div>`;
  area.innerHTML=body;
  el('saveBtn')?.addEventListener('click',saveChanges);
  el('approveBtn')?.addEventListener('click',approve);
  el('exportBtn')?.addEventListener('click',exportPlan);
  el('handoffBtn')?.addEventListener('click',handoff);
}
function formPlan(){
  const p=structuredClone(current.plan);
  document.querySelectorAll('#planArea [data-field]').forEach(e=>p[e.dataset.field]=e.value);
  document.querySelectorAll('#planArea [data-task]').forEach(e=>p.tasks[Number(e.dataset.task)][e.dataset.key]=e.value);
  return p;
}
async function saveChanges(){
  try{const p=formPlan();const data=await api(`/api/runs/${current.id}/plan`,{method:'PUT',body:JSON.stringify(p)});current=data.run;render();message('Draft saved.');return true;}
  catch(e){message(e.message);return false;}
}
async function approve(){if(!(await saveChanges()))return;if(!confirm('Approve this plan as reviewed? You will not be able to edit it afterwards.'))return;try{const data=await api(`/api/runs/${current.id}/approve`,{method:'POST'});current=data.run;render();message('Approved. No content has been published.');}catch(e){message(e.message);}}
async function exportPlan(){try{const data=await api(`/api/runs/${current.id}/export`);const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const href=URL.createObjectURL(blob);const a=document.createElement('a');a.href=href;a.download=`agencyflow-reviewed-${current.id}.json`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),1000);message('Reviewed handoff JSON exported.');}catch(e){message(e.message);}}
async function handoff(){if(!confirm('Send this approved plan to your configured n8n webhook?'))return;try{const d=await api(`/api/runs/${current.id}/handoff`,{method:'POST'});current=d.run;render();message(d.reused?'This plan was already handed off.':'Approved plan delivered to n8n.');}catch(e){message(e.message);}}
async function analyze(){const brief=el('brief').value.trim(),client_name=el('client').value.trim();if(brief.length<30){message('Add at least 30 characters of brief details.');return;}
  const button=el('analyzeBtn');button.disabled=true;el('analyzeText').textContent='Analyzing with AI…';
  try{const data=await api('/api/analyze',{method:'POST',body:JSON.stringify({brief,client_name})});current=data.run;render();message(data.reused?'Duplicate detected — loaded existing plan.':'AI draft ready for your review.');el('planTitle').scrollIntoView({behavior:'smooth',block:'center'});}
  catch(e){message(e.message);}finally{button.disabled=false;el('analyzeText').textContent='Generate production plan';}
}
el('analyzeBtn').addEventListener('click',analyze);
el('brief').addEventListener('input',()=>el('charCount').textContent=`${el('brief').value.length.toLocaleString()} / 6,000`);
document.querySelectorAll('[data-sample]').forEach(b=>b.addEventListener('click',()=>{const s=samples[b.dataset.sample];el('client').value=s.client;el('brief').value=s.brief;el('brief').dispatchEvent(new Event('input'));message('Fictional sample loaded.');}));
el('saveCodeBtn').addEventListener('click',()=>{savedCode=el('accessCode').value;try { sessionStorage.setItem('agencyflow_code',savedCode); } catch (_) {} message('Code saved for this session.');});
initialize();render();
