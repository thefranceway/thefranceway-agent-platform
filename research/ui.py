"""
Research Platform — Single-Page Web UI
Returns a complete HTML document as a string.
Served at GET /research by api_server.py.
"""


def render_ui() -> str:
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Research Platform</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:       #0d0d0d;
  --surface:  #161616;
  --surface2: #1e1e1e;
  --border:   #2a2a2a;
  --accent:   #00d4ff;
  --accent2:  #7c3aed;
  --text:     #e5e5e5;
  --muted:    #888;
  --success:  #22c55e;
  --warn:     #f59e0b;
  --danger:   #ef4444;
}
html, body { height: 100%; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }

/* Layout */
.shell { display: flex; height: 100vh; overflow: hidden; }
.sidebar { width: 260px; min-width: 260px; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

/* Sidebar */
.sidebar-header { padding: 16px; border-bottom: 1px solid var(--border); }
.sidebar-header h1 { font-size: 13px; font-weight: 700; color: var(--accent); letter-spacing: 0.08em; text-transform: uppercase; }
.sidebar-header p  { font-size: 11px; color: var(--muted); margin-top: 2px; }
.btn-new { width: 100%; margin: 12px 0 0; padding: 8px; background: var(--accent); color: #000; border: none; border-radius: 4px; font-size: 13px; font-weight: 700; cursor: pointer; }
.btn-new:hover { background: #00b8d9; }

.project-list { flex: 1; overflow-y: auto; padding: 8px 0; }
.project-item { padding: 10px 16px; cursor: pointer; border-left: 3px solid transparent; transition: all 0.15s; }
.project-item:hover { background: var(--surface2); }
.project-item.active { border-left-color: var(--accent); background: var(--surface2); }
.project-item .proj-title { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.project-item .proj-meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
.project-item .proj-status { display: inline-block; font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 10px; margin-left: 4px; }
.status-draft       { background: #2a2a2a; color: #888; }
.status-in_progress { background: #1a2a3a; color: var(--accent); }
.status-complete    { background: #0f2a1a; color: var(--success); }

/* Topbar */
.topbar { padding: 12px 24px; border-bottom: 1px solid var(--border); background: var(--surface); display: flex; align-items: center; gap: 12px; min-height: 52px; }
.topbar .proj-name { font-size: 15px; font-weight: 700; flex: 1; }
.topbar .proj-id   { font-size: 11px; color: var(--muted); font-family: monospace; }
.btn-delete { padding: 4px 10px; background: transparent; border: 1px solid var(--border); color: var(--danger); border-radius: 4px; cursor: pointer; font-size: 12px; }
.btn-delete:hover { background: #2a0a0a; }

/* Steps nav */
.steps-nav { display: flex; gap: 0; padding: 0 24px; border-bottom: 1px solid var(--border); overflow-x: auto; background: var(--surface); }
.step-tab { padding: 10px 14px; cursor: pointer; border-bottom: 2px solid transparent; font-size: 12px; white-space: nowrap; color: var(--muted); display: flex; align-items: center; gap: 6px; transition: all 0.15s; }
.step-tab:hover { color: var(--text); }
.step-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.step-tab.done   { color: var(--success); }
.step-tab.done.active { color: var(--accent); border-bottom-color: var(--accent); }
.step-dot { width: 18px; height: 18px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; background: var(--border); color: var(--muted); flex-shrink: 0; }
.step-tab.done .step-dot   { background: var(--success); color: #000; }
.step-tab.active .step-dot { background: var(--accent); color: #000; }

/* Content */
.content { flex: 1; overflow-y: auto; padding: 28px 32px; }
.step-panel { max-width: 760px; }
.step-panel h2 { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
.step-desc { color: var(--muted); margin-bottom: 6px; font-size: 13px; }
.step-hint { background: #111; border-left: 3px solid var(--accent); padding: 10px 14px; font-size: 12px; color: #aaa; margin-bottom: 24px; border-radius: 0 4px 4px 0; }

/* Form */
.field { margin-bottom: 18px; }
.field label { display: block; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
.field label .req { color: var(--danger); margin-left: 2px; }
textarea, input[type=text], input[type=number], select {
  width: 100%; background: var(--surface2); border: 1px solid var(--border); border-radius: 4px;
  color: var(--text); padding: 10px 12px; font-size: 13px; font-family: inherit;
  transition: border-color 0.15s; outline: none;
}
textarea { resize: vertical; min-height: 90px; line-height: 1.5; }
textarea:focus, input:focus, select:focus { border-color: var(--accent); }
select option { background: var(--surface2); }

/* Buttons */
.btn-row { display: flex; gap: 10px; margin-top: 24px; align-items: center; }
.btn { padding: 9px 20px; border-radius: 4px; border: none; font-size: 13px; font-weight: 700; cursor: pointer; transition: all 0.15s; }
.btn-primary { background: var(--accent); color: #000; }
.btn-primary:hover { background: #00b8d9; }
.btn-secondary { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent); color: var(--accent); }
.btn-action { background: var(--accent2); color: #fff; padding: 11px 28px; font-size: 14px; }
.btn-action:hover { background: #6d28d9; }
.btn-action:disabled { background: var(--border); color: var(--muted); cursor: not-allowed; }
.btn-download { background: var(--success); color: #000; }
.btn-download:hover { background: #16a34a; }

/* Action panels */
.action-panel { background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; padding: 24px; margin-bottom: 20px; }
.action-panel h3 { font-size: 14px; font-weight: 700; margin-bottom: 8px; }
.action-panel p  { font-size: 13px; color: var(--muted); margin-bottom: 16px; }

/* Output */
.output-block { background: #0a0a0a; border: 1px solid var(--border); border-radius: 4px; padding: 16px; font-family: 'Courier New', monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; color: #c8c8c8; max-height: 420px; overflow-y: auto; margin-top: 16px; }
.output-meta { font-size: 11px; color: var(--muted); font-family: monospace; margin-top: 8px; }

/* Status + alerts */
.alert { padding: 10px 14px; border-radius: 4px; font-size: 13px; margin-bottom: 16px; }
.alert-success { background: #0f2a1a; border: 1px solid var(--success); color: var(--success); }
.alert-error   { background: #2a0a0a; border: 1px solid var(--danger); color: var(--danger); }
.alert-info    { background: #0a1a2a; border: 1px solid var(--accent); color: var(--accent); }
.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite; vertical-align: middle; margin-right: 8px; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Empty state */
.empty-state { text-align: center; padding: 80px 40px; color: var(--muted); }
.empty-state h2 { font-size: 18px; color: var(--text); margin-bottom: 8px; }
.empty-state p { font-size: 13px; margin-bottom: 24px; }

/* Step 3 data format toggle */
.format-toggle { display: flex; gap: 8px; margin-bottom: 12px; }
.fmt-btn { padding: 5px 14px; border-radius: 20px; border: 1px solid var(--border); background: transparent; color: var(--muted); cursor: pointer; font-size: 12px; font-weight: 600; }
.fmt-btn.active { border-color: var(--accent); color: var(--accent); background: #0a1a2a; }
</style>
</head>
<body>
<div class="shell">

  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-header">
      <h1>Research Platform</h1>
      <p>Data Analyst Workspace</p>
      <button class="btn-new" onclick="newProject()">+ New Research</button>
    </div>
    <div class="project-list" id="project-list">
      <div style="padding:20px;color:var(--muted);font-size:12px">Loading…</div>
    </div>
  </div>

  <!-- Main -->
  <div class="main">
    <div class="topbar" id="topbar">
      <div class="empty-state" style="padding:0;text-align:left">
        <span style="color:var(--muted);font-size:13px">Select a project or create a new one →</span>
      </div>
    </div>
    <div class="steps-nav" id="steps-nav" style="display:none"></div>
    <div class="content" id="content">
      <div class="empty-state">
        <h2>Welcome to your Research Platform</h2>
        <p>Create a new research project to begin the 9-step analyst workflow.</p>
        <button class="btn btn-primary" onclick="newProject()">+ New Research</button>
      </div>
    </div>
  </div>

</div>

<script>
const API = '/research-api';
const STEPS = [
  { num:1, key:'research_question', label:'Research Question', icon:'?',
    desc:'Define what you are trying to find out.',
    hint:'Be specific. A good research question is narrow, answerable, and connected to observable data.',
    fields:[
      {key:'question',   label:'Research question',  type:'textarea', required:true,  ph:'e.g. Does sleep duration affect LDL cholesterol in adults over 40?'},
      {key:'background', label:'Background context', type:'textarea', required:false, ph:'Optional: why does this matter?'},
    ]},
  { num:2, key:'hypothesis', label:'Hypothesis', icon:'H',
    desc:'State your expected answer and the null alternative.',
    hint:'Phrase as a directional statement. The null is the default: no effect.',
    fields:[
      {key:'hypothesis',      label:'Hypothesis (H1)',     type:'textarea', required:true,  ph:'e.g. Adults sleeping < 6 hrs will show elevated LDL.'},
      {key:'null_hypothesis', label:'Null hypothesis (H0)',type:'textarea', required:false, ph:'e.g. No significant difference across sleep groups.'},
    ]},
  { num:3, key:'data_collection', label:'Data Collection', icon:'D',
    desc:'Submit your dataset. Supported: CSV, JSON, plain text, or URL.',
    hint:'CSV paste is fastest. Max ~200 rows recommended for in-prompt analysis.',
    special:'data_input',
    fields:[
      {key:'format',      label:'Data format',      type:'select',   required:true,  options:['csv','json','text','url']},
      {key:'source_type', label:'Source',           type:'select',   required:true,  options:['paste','url']},
      {key:'raw',         label:'Paste data',       type:'textarea', required:false, ph:'Paste CSV, JSON, or plain text here…'},
      {key:'url',         label:'Data URL',         type:'text',     required:false, ph:'https://…'},
      {key:'description', label:'What is this data?',type:'text',   required:false, ph:'e.g. Wearable sleep tracker + lab results, n=120'},
    ]},
  { num:4, key:'data_profile', label:'Data Profile', icon:'P',
    desc:'Describe your data structure and quality.',
    hint:'This context helps the agent interpret the data correctly.',
    fields:[
      {key:'summary',       label:'Dataset summary',               type:'textarea', required:true,  ph:'e.g. 120 rows, 8 columns. Sleep and blood panel data.'},
      {key:'columns',       label:'Key columns (comma-separated)', type:'text',     required:false, ph:'e.g. participant_id, age, sleep_hours, ldl_mg_dl'},
      {key:'row_count',     label:'Approximate row count',         type:'number',   required:false, ph:'e.g. 120'},
      {key:'missing_values',label:'Known gaps or missing data',    type:'textarea', required:false, ph:'e.g. 8 participants missing LDL readings'},
      {key:'notes',         label:'Data quality notes',            type:'textarea', required:false, ph:'e.g. Self-reported sleep hours — possible recall bias'},
    ]},
  { num:5, key:'analysis_method', label:'Analysis Method', icon:'M',
    desc:'Choose your analytical approach.',
    hint:'The agent will use your stated method as the primary instruction.',
    fields:[
      {key:'method',         label:'Analysis method',          type:'text',     required:true,  ph:'e.g. Pearson correlation, linear regression, descriptive statistics'},
      {key:'rationale',      label:'Why this method?',         type:'textarea', required:true,  ph:'e.g. Pearson correlation measures the linear relationship between continuous variables.'},
      {key:'independent_var',label:'Independent variable(s)',  type:'text',     required:false, ph:'e.g. sleep_hours'},
      {key:'dependent_var',  label:'Dependent variable(s)',    type:'text',     required:false, ph:'e.g. ldl_mg_dl'},
      {key:'controls',       label:'Control variables',        type:'text',     required:false, ph:'e.g. age, sex, BMI'},
    ]},
  { num:6, key:'agent_analysis', label:'Agent Analysis', icon:'A',
    desc:'DataAnalyticsAgent runs on your data and method.',
    hint:'Click Run Analysis. The agent receives your research question, data, and method. Requires Anthropic API credits.',
    special:'run_analysis', fields:[]},
  { num:7, key:'findings', label:'Findings', icon:'F',
    desc:'Document what the analysis revealed.',
    hint:'Pull from the agent output above. Edit and annotate freely — these are your findings.',
    fields:[
      {key:'key_findings',        label:'Key findings (one per line)',     type:'textarea', required:true,  ph:'e.g.\nSleep duration negatively correlates with LDL (r=-0.42, p<0.01)\nEffect strongest in 45-55 age group'},
      {key:'statistical_results', label:'Statistical results',             type:'textarea', required:false, ph:'e.g. r=-0.42, p=0.003, n=112'},
      {key:'anomalies',           label:'Anomalies or unexpected results', type:'textarea', required:false, ph:'e.g. Participants sleeping >9 hrs also showed elevated LDL.'},
    ]},
  { num:8, key:'conclusions', label:'Conclusions', icon:'C',
    desc:'Interpret your findings in the context of your hypothesis.',
    hint:'Be honest about what the data can and cannot support. Correlation ≠ causation.',
    fields:[
      {key:'conclusion',           label:'Conclusion',             type:'textarea', required:true,  ph:'e.g. Data supports a moderate negative correlation between sleep duration and LDL.'},
      {key:'hypothesis_supported', label:'Hypothesis supported?',  type:'select',   required:true,  options:['yes','no','partial','inconclusive']},
      {key:'limitations',          label:'Limitations',            type:'textarea', required:false, ph:'e.g. Self-reported sleep, no causal mechanism confirmed.'},
      {key:'next_steps',           label:'Recommended next steps', type:'textarea', required:false, ph:'e.g. Replicate with actigraphy data. Control for medication use.'},
    ]},
  { num:9, key:'final_report', label:'Final Report', icon:'R',
    desc:'Generate and export your complete research report.',
    hint:'Compiles all nine steps into a standalone HTML report. Download it, share it, or archive it.',
    special:'generate_report', fields:[]},
];

// App state
let currentProjectId = null;
let currentProject   = null;
let currentStepNum   = 1;

// ── API helpers ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({detail: r.statusText}));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
}

// ── Project list ──────────────────────────────────────────────────────────────
async function loadProjects() {
  const projects = await api('GET', '/projects');
  const el = document.getElementById('project-list');
  if (!projects.length) {
    el.innerHTML = '<div style="padding:16px;color:var(--muted);font-size:12px">No projects yet.</div>';
    return;
  }
  el.innerHTML = projects.map(p => `
    <div class="project-item ${p.id === currentProjectId ? 'active' : ''}"
         onclick="openProject('${p.id}')">
      <div class="proj-title">${escHtml(p.title)}</div>
      <div class="proj-meta">
        ${p.steps_done}/9 steps
        <span class="proj-status status-${p.status}">${p.status.replace('_',' ')}</span>
      </div>
    </div>`).join('');
}

// ── Open / create ─────────────────────────────────────────────────────────────
async function openProject(id) {
  currentProjectId = id;
  currentProject   = await api('GET', `/projects/${id}`);
  currentStepNum   = currentProject.current_step;
  renderProject();
  loadProjects(); // refresh sidebar
}

async function newProject() {
  const title = prompt('Research title:');
  if (!title || !title.trim()) return;
  const p = await api('POST', '/projects', {title: title.trim()});
  currentProjectId = p.id;
  currentProject   = p;
  currentStepNum   = 1;
  renderProject();
  loadProjects();
}

async function deleteProject() {
  if (!currentProjectId) return;
  if (!confirm(`Delete "${currentProject.title}"? This cannot be undone.`)) return;
  await api('DELETE', `/projects/${currentProjectId}`);
  currentProjectId = null;
  currentProject   = null;
  document.getElementById('topbar').innerHTML = `<span style="color:var(--muted);font-size:13px">Select a project or create a new one →</span>`;
  document.getElementById('steps-nav').style.display = 'none';
  document.getElementById('content').innerHTML = `<div class="empty-state"><h2>Project deleted</h2><p>Create a new research project to continue.</p><button class="btn btn-primary" onclick="newProject()">+ New Research</button></div>`;
  loadProjects();
}

// ── Render project ─────────────────────────────────────────────────────────────
function renderProject() {
  // Topbar
  document.getElementById('topbar').innerHTML = `
    <div class="proj-name">${escHtml(currentProject.title)}</div>
    <div class="proj-id">${currentProject.id}</div>
    <button class="btn-delete" onclick="deleteProject()">Delete</button>`;

  // Steps nav
  const nav = document.getElementById('steps-nav');
  nav.style.display = 'flex';
  nav.innerHTML = STEPS.map(s => {
    const done    = currentProject.steps[s.key]?.completed;
    const active  = s.num === currentStepNum;
    const locked  = s.num > currentProject.current_step;
    return `<div class="step-tab ${active?'active':''} ${done?'done':''}"
                 onclick="${locked ? '' : `goStep(${s.num})`}"
                 style="${locked ? 'opacity:0.4;cursor:default' : ''}">
      <div class="step-dot">${done ? '✓' : s.num}</div>
      ${s.label}
    </div>`;
  }).join('');

  renderStep(currentStepNum);
}

function goStep(num) {
  currentStepNum = num;
  renderProject();
}

// ── Render step ───────────────────────────────────────────────────────────────
function renderStep(num) {
  const step    = STEPS[num - 1];
  const content = document.getElementById('content');

  if (step.special === 'run_analysis') {
    renderAnalysisStep(step, content);
  } else if (step.special === 'generate_report') {
    renderReportStep(step, content);
  } else {
    renderFormStep(step, content);
  }
}

function renderFormStep(step, content) {
  const saved   = currentProject.steps[step.key]?.data || {};
  const isFirst = step.num === 1;
  const isLast  = step.num === 9;

  const fieldsHtml = step.fields.map(f => {
    const val = saved[f.key] || '';
    const req  = f.required ? '<span class="req">*</span>' : '';

    if (f.type === 'textarea') {
      return `<div class="field">
        <label>${f.label}${req}</label>
        <textarea id="f_${f.key}" placeholder="${f.ph||''}" rows="4">${escHtml(val)}</textarea>
      </div>`;
    }
    if (f.type === 'select') {
      const opts = (f.options||[]).map(o =>
        `<option value="${o}" ${val===o?'selected':''}>${o}</option>`).join('');
      return `<div class="field"><label>${f.label}${req}</label><select id="f_${f.key}">${opts}</select></div>`;
    }
    if (f.type === 'number') {
      return `<div class="field"><label>${f.label}${req}</label><input type="number" id="f_${f.key}" value="${escHtml(val)}" placeholder="${f.ph||''}"></div>`;
    }
    // default: text
    return `<div class="field"><label>${f.label}${req}</label><input type="text" id="f_${f.key}" value="${escHtml(val)}" placeholder="${f.ph||''}"></div>`;
  }).join('');

  const isStep3 = step.special === 'data_input';
  const prevBtn = step.num > 1 ? `<button class="btn btn-secondary" onclick="goStep(${step.num-1})">← Back</button>` : '';

  content.innerHTML = `
    <div class="step-panel">
      <h2>Step ${step.num} — ${step.label}</h2>
      <p class="step-desc">${step.desc}</p>
      <div class="step-hint">${step.hint}</div>
      <div id="step-alert"></div>
      <form id="step-form" onsubmit="return false">
        ${isStep3 ? renderDataInputToggle(step, saved) : fieldsHtml}
        <div class="btn-row">
          ${prevBtn}
          <button class="btn btn-primary" onclick="saveStep('${step.key}', ${step.num})">
            ${step.num === 8 ? 'Save & Go to Report →' : 'Save & Continue →'}
          </button>
          <span id="save-status" style="font-size:12px;color:var(--muted)"></span>
        </div>
      </form>
    </div>`;

  if (isStep3) bindStep3Logic(saved);
}

function renderDataInputToggle(step, saved) {
  // Special handling for step 3 — show/hide paste vs URL based on source_type
  const fmt = saved.format || 'csv';
  const src = saved.source_type || 'paste';
  return `
    <div class="field">
      <label>Data format</label>
      <div class="format-toggle">
        ${['csv','json','text','url'].map(f =>
          `<button type="button" class="fmt-btn ${fmt===f?'active':''}" id="fmt-${f}" onclick="setFormat('${f}')">${f.toUpperCase()}</button>`
        ).join('')}
      </div>
      <input type="hidden" id="f_format" value="${fmt}">
    </div>
    <div class="field">
      <label>Source</label>
      <div class="format-toggle">
        ${['paste','url'].map(s =>
          `<button type="button" class="fmt-btn ${src===s?'active':''}" id="src-${s}" onclick="setSource('${s}')">${s.toUpperCase()}</button>`
        ).join('')}
      </div>
      <input type="hidden" id="f_source_type" value="${src}">
    </div>
    <div id="paste-area" style="${src==='url'?'display:none':''}">
      <div class="field">
        <label>Paste data</label>
        <textarea id="f_raw" placeholder="Paste CSV, JSON, or plain text here…" rows="10" style="font-family:monospace;font-size:12px">${escHtml(saved.raw||'')}</textarea>
      </div>
    </div>
    <div id="url-area" style="${src!=='url'?'display:none':''}">
      <div class="field">
        <label>Data URL</label>
        <input type="text" id="f_url" value="${escHtml(saved.url||'')}" placeholder="https://…">
      </div>
    </div>
    <div class="field">
      <label>What is this data?</label>
      <input type="text" id="f_description" value="${escHtml(saved.description||'')}" placeholder="e.g. Wearable sleep tracker export + lab results, n=120">
    </div>`;
}

function bindStep3Logic() {}
function setFormat(f) {
  document.getElementById('f_format').value = f;
  ['csv','json','text','url'].forEach(x => {
    document.getElementById('fmt-'+x).classList.toggle('active', x===f);
  });
  if (f === 'url') setSource('url');
}
function setSource(s) {
  document.getElementById('f_source_type').value = s;
  ['paste','url'].forEach(x => {
    document.getElementById('src-'+x).classList.toggle('active', x===s);
  });
  document.getElementById('paste-area').style.display = s==='url'?'none':'';
  document.getElementById('url-area').style.display   = s==='url'?'':'none';
}

// ── Save step ─────────────────────────────────────────────────────────────────
async function saveStep(stepKey, stepNum) {
  const step    = STEPS[stepNum - 1];
  const data    = {};
  let   hasReq  = true;
  let   missing = [];

  step.fields.forEach(f => {
    const el = document.getElementById('f_' + f.key);
    if (!el) return;
    const val = el.value.trim();
    data[f.key] = el.type === 'number' ? (val ? Number(val) : null) : val;
    if (f.required && !val) { hasReq = false; missing.push(f.label); }
  });

  if (!hasReq) {
    showAlert('step-alert', 'error', `Required: ${missing.join(', ')}`);
    return;
  }

  const statusEl = document.getElementById('save-status');
  statusEl.innerHTML = '<span class="spinner"></span>Saving…';

  try {
    currentProject = await api('PUT', `/projects/${currentProjectId}/step/${stepKey}`, {data});
    statusEl.textContent = '✓ Saved';
    setTimeout(() => statusEl.textContent = '', 2000);

    // Advance to next step
    if (stepNum < 9) {
      currentStepNum = stepNum + 1;
      renderProject();
    }
  } catch(e) {
    showAlert('step-alert', 'error', e.message);
    statusEl.textContent = '';
  }
}

// ── Step 6: Agent Analysis ────────────────────────────────────────────────────
function renderAnalysisStep(step, content) {
  const aa     = currentProject.steps.agent_analysis?.data || {};
  const output = aa.raw_output || '';
  const hasRun = Boolean(output);

  content.innerHTML = `
    <div class="step-panel">
      <h2>Step 6 — Agent Analysis</h2>
      <p class="step-desc">${step.desc}</p>
      <div class="step-hint">${step.hint}</div>
      <div id="step-alert"></div>
      <div class="action-panel">
        <h3>DataAnalyticsAgent</h3>
        <p>The agent will receive your research question, dataset, and analysis method, then return statistical findings and key patterns.</p>
        <button class="btn btn-action" id="run-btn" onclick="runAnalysis()">
          ${hasRun ? '↻ Re-run Analysis' : '▶ Run Analysis'}
        </button>
        ${hasRun ? `<div class="output-meta">Last run: ${aa.run_id || '—'} · ${aa.tool_calls||0} tool calls · ${aa.iterations||0} iterations</div>` : ''}
      </div>
      ${hasRun ? `<div class="output-block" id="analysis-output">${escHtml(output)}</div>` : '<div id="analysis-output"></div>'}
      <div class="btn-row" style="margin-top:20px">
        <button class="btn btn-secondary" onclick="goStep(5)">← Back</button>
        ${hasRun ? '<button class="btn btn-primary" onclick="goStep(7)">Next: Findings →</button>' : ''}
      </div>
    </div>`;
}

async function runAnalysis() {
  const btn = document.getElementById('run-btn');
  const out = document.getElementById('analysis-output');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Running…';
  out.innerHTML = '';
  document.getElementById('step-alert').innerHTML = '';

  try {
    const result = await api('POST', `/projects/${currentProjectId}/analyze`);
    currentProject = result.project;
    out.textContent = result.output;
    out.className = 'output-block';
    btn.disabled = false;
    btn.innerHTML = '↻ Re-run Analysis';
    renderProject(); // re-render to show meta + Next button
  } catch(e) {
    btn.disabled = false;
    btn.innerHTML = '▶ Run Analysis';
    showAlert('step-alert', 'error', e.message);
  }
}

// ── Step 9: Report ────────────────────────────────────────────────────────────
function renderReportStep(step, content) {
  const fr      = currentProject.steps.final_report?.data || {};
  const hasRep  = Boolean(fr.generated_at);

  content.innerHTML = `
    <div class="step-panel">
      <h2>Step 9 — Final Report</h2>
      <p class="step-desc">${step.desc}</p>
      <div class="step-hint">${step.hint}</div>
      <div id="step-alert"></div>
      <div class="action-panel">
        <h3>Generate Report</h3>
        <p>Compiles all steps — research question, hypothesis, dataset, method, agent analysis, findings, and conclusions — into a standalone HTML document.</p>
        <div class="btn-row">
          <button class="btn btn-action" id="gen-btn" onclick="generateReport()">
            ${hasRep ? '↻ Regenerate Report' : '⬡ Generate Report'}
          </button>
          ${hasRep ? `<a class="btn btn-download" href="${API}/projects/${currentProjectId}/report" download>↓ Download HTML</a>` : ''}
        </div>
        ${hasRep ? `<div class="output-meta" style="margin-top:10px">Generated: ${fr.generated_at?.slice(0,19).replace('T',' ')} UTC</div>` : ''}
      </div>
      <div id="report-preview"></div>
      <div class="btn-row">
        <button class="btn btn-secondary" onclick="goStep(8)">← Back</button>
      </div>
    </div>`;
}

async function generateReport() {
  const btn = document.getElementById('gen-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Generating…';

  try {
    const res  = await fetch(`${API}/projects/${currentProjectId}/report`, {method:'POST'});
    if (!res.ok) throw new Error(await res.text());
    const html = await res.text();

    // Show preview in iframe
    const preview = document.getElementById('report-preview');
    preview.innerHTML = `
      <div style="margin-top:20px;border:1px solid var(--border);border-radius:4px;overflow:hidden">
        <div style="padding:8px 12px;background:var(--surface2);font-size:12px;color:var(--muted)">Preview — <a href="${API}/projects/${currentProjectId}/report" download style="color:var(--accent)">Download HTML →</a></div>
        <iframe srcdoc="${escAttr(html)}" style="width:100%;height:500px;border:none;background:white"></iframe>
      </div>`;

    currentProject = await api('GET', `/projects/${currentProjectId}`);
    btn.disabled = false;
    btn.innerHTML = '↻ Regenerate Report';
    renderProject();
  } catch(e) {
    btn.disabled = false;
    btn.innerHTML = '⬡ Generate Report';
    showAlert('step-alert', 'error', e.message);
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
}
function showAlert(id, type, msg) {
  document.getElementById(id).innerHTML = `<div class="alert alert-${type}">${escHtml(msg)}</div>`;
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadProjects();
</script>
</body>
</html>"""
