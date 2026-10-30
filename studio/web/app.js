// Asset Jam Studio front-end: one page per generation flow, live progress over server-sent events.
const $ = (s, r = document) => r.querySelector(s);
const state = {
  page: location.hash.slice(1) || 'overview',
  system: null,
  jobs: new Map(),
  library: { model3d: [], texture: [], sprite: [], voice: [], music: [], sfx: [], video: [], anim: [] },
  libLoaded: {},
  voices: [], tips: null, music: null, sfx: null, video: null, anim: null,
  selected: {},          // kind -> library item key
  focus: {},             // kind -> job id shown in the activity card
  forms: {},             // kind -> form state (uploads etc.)
};
let viewerMod = null;
const viewers = new Map();

// ------------------------------------------------------------------ helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k in el && k !== 'list' && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}
const api = {
  async get(p) { const r = await fetch(p); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText); return r.json(); },
  async post(p, body) { const r = await fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || r.statusText); return j; },
  async upload(file) { const r = await fetch('/api/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file }); const j = await r.json(); if (!r.ok) throw new Error(j.error); return j; },
};
function toast(msg, cls = '') { const t = h('div', { class: 'toast ' + cls }, msg); $('#toasts').append(t); setTimeout(() => t.remove(), 5000); }
function fmtDate(ts) { if (!ts) return ''; const d = new Date(ts * 1000); return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }); }
function fmtDur(s) { if (s == null) return ''; s = Math.round(s); return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's'; }
function elapsed(job) { if (!job.started) return ''; return fmtDur((job.finished || Date.now() / 1000) - job.started); }
function basename(p) { return (p || '').split(/[\\/]/).pop(); }
function modal(content) { const m = $('#modal'), box = $('#modal-box'); box.innerHTML = ''; box.append(content); m.classList.remove('hidden'); }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') $('#modal').classList.add('hidden'); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').classList.add('hidden'); });
function lightbox(url) { modal(h('img', { src: url })); }
const KIND_LABEL = { model3d: '3D model', texture: 'Texture', sprite: 'Sprite', voice: 'Voice line', voice_prep: 'Voice prep', paint: 'Painted prop', prop: 'Modelled prop', music: 'Music', sfx: 'Sound effect', video: 'Video', anim: 'Character' };
const AUDIO_KINDS = ['voice', 'music', 'sfx'];
const pageOf = kind => kind === 'voice_prep' ? 'voice' : kind === 'paint' || kind === 'prop' ? 'model3d' : kind;   // voice preparation jobs show on the Voice page, painted props on 3D Models

// ------------------------------------------------------------------ events
let es;
function connect() {
  es = new EventSource('/api/events');
  es.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.type === 'hello' || m.type === 'system') { state.system = m.system; renderStatus(); if (state.page === 'overview') renderOverview(); }
    else if (m.type === 'job') {
      const old = state.jobs.get(m.job.id);
      const prevStatus = old ? old.status : null;
      const j = Object.assign(old || {}, m.job, { log: old && old.log && old.log.length > m.job.log.length ? old.log : m.job.log });
      state.jobs.set(j.id, j);
      if (!old || prevStatus === 'running' || prevStatus === 'queued') state.focus[pageOf(j.kind)] = j.id;
      if (old && prevStatus !== j.status && ['done', 'error', 'cancelled'].includes(j.status)) {
        if (j.status === 'done') { toast(`${KIND_LABEL[j.kind]} ready: ${j.title}`, 'ok'); loadLibrary(pageOf(j.kind), true); if (j.kind === 'voice_prep') closeVoicePanels(); }
        else if (j.status === 'error') toast(`${KIND_LABEL[j.kind]} failed: ${j.error || ''}`.slice(0, 200), 'err');
      }
      scheduleRender();
    }
    else if (m.type === 'log') { const j = state.jobs.get(m.id); if (j) { (j.log = j.log || []).push(m.line); const lg = document.querySelector(`[data-log="${m.id}"]`); if (lg) { lg.textContent += (lg.textContent ? '\n' : '') + m.line; lg.scrollTop = lg.scrollHeight; } } }
    else if (m.type === 'preview') { const img = document.querySelector(`[data-preview="${m.id}"]`); if (img) { img.src = `/api/jobs/${m.id}/preview?seq=${m.seq}`; img.parentElement.classList.remove('hidden'); } }
    else if (m.type === 'job_removed') { state.jobs.delete(m.id); scheduleRender(); }
  };
  es.onerror = () => { es.close(); setTimeout(connect, 2000); };
}
let renderQueued = false;
function scheduleRender() { if (renderQueued) return; renderQueued = true; setTimeout(() => { renderQueued = false; renderPage(); }, 16); }   // not rAF: it pauses in background tabs

async function loadState() {
  const s = await api.get('/api/state');
  state.system = s.system;
  for (const j of s.jobs) state.jobs.set(j.id, j);
  for (const k of Object.keys(KIND_LABEL)) { const js = jobsOf(k); if (js.length && !state.focus[k]) state.focus[k] = js[0].id; }
}
function jobsOf(kind) { return [...state.jobs.values()].filter(j => j.kind === kind).sort((a, b) => b.created - a.created); }
async function loadLibrary(kind, force = false) {
  if (state.libLoaded[kind] && !force) return;
  const path = { model3d: 'models', texture: 'textures', sprite: 'sprites', voice: 'voice', music: 'music', sfx: 'sfx', video: 'videos', anim: 'characters' }[kind];
  state.library[kind] = await api.get('/api/library/' + path);
  state.libLoaded[kind] = true;
  if (kind === 'anim') { if (!state.libLoaded.model3d) await loadLibrary('model3d'); if (!state.anim || force) { state.anim = await api.get('/api/anim'); buildAnimForm(); } }
  if (kind === 'voice' && (!state.voices.length || force)) { const v = await api.get('/api/voices'); state.voices = v.voices; state.tips = v.tips; buildVoiceForm(); }
  if (kind === 'music' && (!state.music || force)) { state.music = await api.get('/api/music'); buildMusicForm(); }
  if (kind === 'sfx' && (!state.sfx || force)) { state.sfx = await api.get('/api/sfx'); buildSfxForm(); }
  if (kind === 'video' && !state.video) { state.video = await api.get('/api/video'); buildVideoForm(); }
  scheduleRender();
}

// ------------------------------------------------------------------ status bar
// chips: dot + name (.txt) + state (.val) + optional button; the CSS hides .txt/.val on narrow windows, the title keeps the detail
function chip(title, dot, name, val, button, extra) {
  return h('span', { class: 'chip', title: `${title} · ${val}` }, h('i', { class: 'dot ' + dot }), h('span', { class: 'txt' }, name + ' '), h('b', { class: 'val' }, val), extra, button);
}
function workerChip(label, w, title, stopPath) {
  return chip(title, w.model_loaded ? 'ok' : w.process === 'running' ? 'warn' : '', label, w.model_loaded ? 'loaded' : w.process === 'running' ? 'idle' : 'off',
    w.process === 'running' ? h('button', { class: 'btn xs', onclick: () => api.post(stopPath).then(() => toast(`${label.toLowerCase()} worker stopped`)).catch(e => toast(e.message, 'err')) }, 'Stop') : null);
}
function comfyButton(c, cls = 'btn xs') {
  if (c.alive) return h('button', { class: cls, onclick: () => api.post('/api/comfy/free').then(() => toast('asked ComfyUI to unload its models')).catch(e => toast(e.message, 'err')), title: 'Unload models from VRAM' }, 'Free VRAM');
  if (c.process !== 'starting') return h('button', { class: cls + ' primary', onclick: () => api.post('/api/comfy/start').then(() => toast('starting ComfyUI, takes ~20 s')).catch(e => toast(e.message, 'err')) }, 'Start');
  return null;
}
function renderStatus() {
  const s = state.system; if (!s) return;
  const el = $('#status'); el.innerHTML = '';
  const c = s.comfy, g = s.gpu;
  el.append(chip('ComfyUI runs the image, 3D, texture, sprite and video models', c.alive ? 'ok' : c.process === 'starting' ? 'warn' : 'err', 'ComfyUI',
    c.alive ? 'running' : c.process === 'starting' ? 'starting…' : 'stopped', comfyButton(c)));
  el.append(workerChip('Voice', s.tts || {}, 'Qwen3-TTS worker process (ai-voc)', '/api/tts/stop'));
  el.append(workerChip('Music', s.music || {}, 'ACE-Step 1.5 worker process (ai-mus)', '/api/music/stop'));
  el.append(workerChip('SFX', s.sfx || {}, 'MOSS-SoundEffect v2.0 worker process (ai-sfx)', '/api/sfx/stop'));
  el.append(workerChip('Animate', s.anim || {}, 'Rig + motion worker process (ai-anim: Make-It-Animatable, Kimodo)', '/api/anim/stop'));
  if (g.available) el.append(h('span', { class: 'chip', title: `${g.name} · ${(g.used_mb / 1024).toFixed(1)} / ${(g.total_mb / 1024).toFixed(0)} GB · ${g.util}% busy` }, h('span', { class: 'txt' }, 'GPU '), h('b', { class: 'val' }, `${(g.used_mb / 1024).toFixed(1)} / ${(g.total_mb / 1024).toFixed(0)} GB`), h('span', { class: 'vrambar' }, h('i', { style: { width: Math.round(100 * g.used_mb / g.total_mb) + '%' } })), h('span', { class: 'dim val' }, g.util + '%')));
  const run = s.running ? state.jobs.get(s.running) : null;
  el.append(h('span', { class: 'chip', onclick: () => go('overview'), style: { cursor: 'pointer' }, title: run ? `Running: ${run.title}` : 'Idle' }, h('i', { class: 'dot ' + (run ? 'busy' : '') }), h('span', { class: 'txt' }, run ? `Running: ${run.title.slice(0, 32)}` : 'Idle'), s.queued.length ? h('b', {}, ` +${s.queued.length}`) : null));
}

// ------------------------------------------------------------------ accent colour (saved in localStorage; style.css derives the rest from --accent)
const ACCENTS = ['#6d5dfc', '#4f8cff', '#19b5c8', '#2fbf71', '#d9a420', '#f0742a', '#e8456a', '#c552e0', '#8a8fa8', '#5b7cfa', '#2cc4a0', '#ff9f43'];
function currentAccent() { try { return localStorage.getItem('accent') || ACCENTS[0]; } catch (e) { return ACCENTS[0]; } }
function applyAccent(hex, save = true) {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return;
  document.documentElement.style.setProperty('--accent', hex);
  if (save) { try { localStorage.setItem('accent', hex); } catch (e) { /* private window: the colour lasts for this page only */ } }
  document.querySelectorAll('.swatch').forEach(sw => sw.classList.toggle('on', sw.dataset.hex.toLowerCase() === hex.toLowerCase()));
}
function openAccentPicker(anchor) {
  const old = $('.popover'); if (old) { old.remove(); return; }
  const cur = currentAccent();
  const swatches = h('div', { class: 'swatches' }, ACCENTS.map(c => h('span', { class: 'swatch' + (c.toLowerCase() === cur.toLowerCase() ? ' on' : ''), 'data-hex': c, style: { background: c }, title: c, onclick: () => applyAccent(c) })));
  const custom = h('input', { type: 'color', value: cur, oninput: e => applyAccent(e.target.value) });
  const pop = h('div', { class: 'popover' }, h('h3', {}, 'Accent colour'), swatches,
    h('div', { class: 'row' }, custom, h('span', { class: 'dim small grow' }, 'any colour'), h('button', { class: 'btn xs', onclick: () => { applyAccent(ACCENTS[0]); custom.value = ACCENTS[0]; } }, 'Reset')),
    h('div', { class: 'hint' }, 'Saved in this browser only.'));
  document.body.append(pop);
  const r = anchor.getBoundingClientRect(); pop.style.top = (r.bottom + 8) + 'px'; pop.style.left = Math.max(8, r.right - pop.offsetWidth) + 'px';
  const close = e => { if (!pop.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) { pop.remove(); document.removeEventListener('mousedown', close); } };
  setTimeout(() => document.addEventListener('mousedown', close));
}
$('#accent-btn').addEventListener('click', e => openAccentPicker(e.currentTarget));
applyAccent(currentAccent(), false);

// ------------------------------------------------------------------ navigation
function go(page) { state.page = page; location.hash = page; for (const b of $('#nav').children) b.classList.toggle('active', b.dataset.page === page); for (const p of document.querySelectorAll('.page')) p.classList.toggle('active', p.dataset.page === page); if (page !== 'overview') loadLibrary(page); renderPage(); }
$('#nav').addEventListener('click', e => { const b = e.target.closest('button'); if (b) go(b.dataset.page); });
function renderPage() { ({ overview: renderOverview, model3d: () => renderFlow('model3d'), texture: () => renderFlow('texture'), sprite: () => renderFlow('sprite'), voice: () => renderFlow('voice'), music: () => renderFlow('music'), sfx: () => renderFlow('sfx'), video: () => renderFlow('video'), anim: () => renderFlow('anim') })[state.page](); renderStatus(); }

// ------------------------------------------------------------------ shared UI pieces
function field(label, input, hint) { return h('label', { class: 'field' }, h('span', {}, label), input, hint ? h('div', { class: 'hint' }, hint) : null); }
function select(name, options, value) { const s = h('select', { name }); for (const o of options) { const [v, l] = Array.isArray(o) ? o : [o, o]; s.append(h('option', { value: v, selected: String(v) === String(value) }, l)); } return s; }
function seg(name, options, value) {
  const el = h('div', { class: 'seg', 'data-name': name }); el.dataset.value = value;
  for (const [v, l, title] of options) el.append(h('button', { type: 'button', class: String(v) === String(value) ? 'on' : '', title, onclick: () => { el.dataset.value = v; for (const b of el.children) b.classList.toggle('on', b.textContent === l); el.dispatchEvent(new Event('change', { bubbles: true })); } }, l));
  return el;
}
function check(name, label, checked, hint) { return h('label', { class: 'check', title: hint }, h('input', { type: 'checkbox', name, checked }), label); }
function num(name, value, attrs = {}) { return h('input', Object.assign({ type: 'number', name, value: value ?? '' }, attrs)); }
function text(name, value, attrs = {}) { return h('input', Object.assign({ type: 'text', name, value: value ?? '' }, attrs)); }
function seedField(name = 'seed') { return field('Seed', h('div', { class: 'row' }, h('input', { type: 'number', name, placeholder: 'random', class: 'grow', style: { flex: '1' } }), h('button', { type: 'button', class: 'btn sm', title: 'Pick a random seed now', onclick: e => { e.target.previousSibling.value = Math.floor(Math.random() * 2 ** 31); } }, '🎲')), 'Empty = random. Same prompt + same seed = same result.'); }
function readForm(form) {
  const out = {};
  for (const el of form.querySelectorAll('input[name], select[name], textarea[name]')) {
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else if (el.type === 'number') out[el.name] = el.value === '' ? null : Number(el.value);
    else out[el.name] = el.value;
  }
  for (const el of form.querySelectorAll('.seg[data-name]')) out[el.dataset.name] = el.dataset.value;
  return out;
}
function fillForm(form, params) {
  for (const [k, v] of Object.entries(params || {})) {
    const el = form.querySelector(`[name="${k}"]`);
    if (el) { if (el.type === 'checkbox') el.checked = !!v; else el.value = v ?? ''; el.dispatchEvent(new Event('input', { bubbles: true })); continue; }
    const sg = form.querySelector(`.seg[data-name="${k}"]`);
    if (sg) { sg.dataset.value = v; [...sg.children].forEach(b => b.classList.toggle('on', false)); const btn = [...sg.children].find(b => b.dataset.v === String(v)); if (btn) btn.classList.add('on'); }
  }
}
// seg buttons remember their value for fillForm
function segf(name, options, value) { const el = seg(name, options, value); [...el.children].forEach((b, i) => b.dataset.v = String(options[i][0])); return el; }

function uploadBox(kind, key, label, opts = {}) {
  // a drop zone bound to state.forms[kind][key] = {path, url, name}
  const fs = state.forms[kind] = state.forms[kind] || {};
  const inp = h('input', { type: 'file', accept: 'image/*', class: 'hidden' });
  const box = h('div', { class: 'drop' });
  const render = () => {
    const v = fs[key]; box.innerHTML = '';
    if (v) box.append(h('img', { src: v.url }), h('div', { class: 'txt' }, h('b', {}, v.name || basename(v.path)), h('br'), h('span', { class: 'dim' }, label)), h('span', { class: 'x', title: 'remove', onclick: e => { e.stopPropagation(); delete fs[key]; render(); } }, '✕'));
    else box.append(h('div', { class: 'txt' }, h('b', {}, label), h('br'), opts.hint || 'Drop a PNG here or click to choose', opts.pick ? h('span', {}, ' · ', h('a', { href: '#', onclick: e => { e.preventDefault(); e.stopPropagation(); opts.pick(v2 => { fs[key] = v2; render(); }); } }, 'pick from library')) : null));
  };
  box.onclick = () => inp.click();
  box.ondragover = e => { e.preventDefault(); box.classList.add('over'); };
  box.ondragleave = () => box.classList.remove('over');
  const take = async file => { if (!file) return; try { const u = await api.upload(file); fs[key] = u; render(); } catch (e) { toast('upload failed: ' + e.message, 'err'); } };
  box.ondrop = e => { e.preventDefault(); box.classList.remove('over'); take(e.dataTransfer.files[0]); };
  inp.onchange = () => take(inp.files[0]);
  render();
  const wrap = h('div', { class: 'field' }, box, inp);
  wrap.refresh = render;
  return wrap;
}
function pickSprite(cb) {
  loadLibrary('sprite').then(() => {
    const grid = h('div', { class: 'grid', style: { width: '720px', maxWidth: '86vw' } });
    for (const it of state.library.sprite) grid.append(h('div', { class: 'tile', onclick: () => { cb({ path: it.path, url: it.url, name: it.file }); $('#modal').classList.add('hidden'); } }, h('div', { class: 'img' }, h('img', { src: it.url })), h('div', { class: 'cap' }, h('b', {}, it.name))));
    modal(h('div', {}, h('h2', { style: { marginBottom: '12px' } }, 'Pick a sprite'), grid));
  });
}

// progress block shared by all activity cards
function progressBlock(job) {
  const p = job.progress || {};
  const indet = job.status === 'running' && !(p.max > 0) && p.pct < 2;
  const wrap = h('div', { class: 'pb' },
    h('div', { class: 'progress' + (indet ? ' indet' : '') }, h('i', { style: { width: (job.status === 'done' ? 100 : p.pct || 0) + '%' } })),
    h('div', { class: 'phase' }, h('span', {}, h('b', {}, p.phase || ''), p.detail ? ' · ' + p.detail : ''), h('span', {}, p.max > 0 ? `${p.value}/${p.max} · ` : '', `${Math.round(job.status === 'done' ? 100 : p.pct || 0)}% · ${elapsed(job)}`)));
  wrap.update = j => { const q = j.progress || {}; const bar = wrap.querySelector('.progress'); bar.classList.toggle('indet', j.status === 'running' && !(q.max > 0) && q.pct < 2); bar.firstChild.style.width = (j.status === 'done' ? 100 : q.pct || 0) + '%'; const sp = wrap.querySelectorAll('.phase > span'); sp[0].innerHTML = ''; sp[0].append(h('b', {}, q.phase || ''), q.detail ? ' · ' + q.detail : ''); sp[1].textContent = `${q.max > 0 ? q.value + '/' + q.max + ' · ' : ''}${Math.round(j.status === 'done' ? 100 : q.pct || 0)}% · ${elapsed(j)}`; };
  return wrap;
}
function jobActions(job) {
  const a = [];
  if (job.status === 'running' || job.status === 'queued') a.push(h('button', { class: 'btn sm danger', onclick: () => api.post(`/api/jobs/${job.id}/cancel`) }, job.cancel_requested ? 'Cancelling…' : 'Cancel'));
  else if (job.kind in state.library || job.kind === 'paint' || job.kind === 'prop') a.push(h('button', { class: 'btn sm', title: 'Put these settings back into the form', onclick: () => reuse(job) }, '↻ Reuse settings'));
  if (job.status !== 'running') a.push(h('button', { class: 'btn sm', title: 'Remove this job record (files stay)', onclick: () => api.post(`/api/jobs/${job.id}/delete`).then(() => { state.jobs.delete(job.id); if (state.focus[job.kind] === job.id) delete state.focus[job.kind]; scheduleRender(); }) }, 'Forget'));
  return a;
}
function reuse(job) {
  if (job.kind === 'video') { const p = job.params; videoFormFromSpec(Object.assign({}, p, { audio: p.audio_upload, shots: p.shots })); buildVideoForm(); $('#video-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); toast('shot list loaded into the form'); return; }
  if (job.kind === 'prop') { show3dFlow('prop'); const p = job.params; const fs = state.forms.prop = state.forms.prop || {}; fs.reference = p.reference_upload ? { path: p.reference_upload, name: p.reference_name || basename(p.reference_upload) } : p.reference_url ? { url: p.reference_url, name: p.reference_name } : null; if (!fs.reference) delete fs.reference; const form = $('#prop-form'); fillForm(form, p); for (const u of form.querySelectorAll('.field')) if (u.refresh) u.refresh(); form.scrollIntoView({ behavior: 'smooth', block: 'start' }); toast('settings loaded into the form'); return; }
  if (job.kind === 'paint') { show3dFlow('paint'); const p = job.params; const fs = state.forms.paint = state.forms.paint || {}; fs.model = p.model_upload ? { path: p.model_upload, name: p.model_name || basename(p.model_upload) } : p.model_url ? { url: p.model_url, name: p.model_name } : null; fs.picture = p.picture_upload ? { path: p.picture_upload, url: p.picture_url, name: p.picture_name || basename(p.picture_upload) } : p.picture_url ? { url: p.picture_url, name: p.picture_name } : null; const form = $('#paint-form'); fillForm(form, p); for (const u of form.querySelectorAll('.field')) if (u.refresh) u.refresh(); form.scrollIntoView({ behavior: 'smooth', block: 'start' }); toast('settings loaded into the paint form'); return; }
  if (job.kind === 'anim') { const p = job.params; const fs = state.forms.anim = state.forms.anim || {}; fs.source = p.mesh_upload ? { path: p.mesh_upload, url: p.mesh_url, name: basename(p.mesh_upload) } : p.source ? { path: p.source, url: p.source_url, name: p.source_name || basename(p.source) } : null; fs.clips = (p.clips || []).map(c => Object.assign({}, c)); fs.values = Object.assign({}, p); buildAnimForm(); $('#anim-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); toast('settings loaded into the form'); return; }
  if (job.kind === 'model3d') show3dFlow('model3d');
  const form = job.kind === 'model3d' ? $('#model3d-form') : $(`#page-${job.kind} form`);
  const p = Object.assign({}, job.params);
  const fs = state.forms[job.kind] = state.forms[job.kind] || {};
  if (job.kind === 'model3d') { fs.reference = p.reference_upload ? { path: p.reference_upload, url: job.params.reference_url, name: basename(p.reference_upload) } : null; if (!fs.reference) delete fs.reference; }
  if (job.kind === 'music') { if (p.reference_upload) fs.reference = { path: p.reference_upload, url: p.reference_url, name: basename(p.reference_upload) }; else delete fs.reference; }
  if (job.kind === 'texture') { if (p.reference_upload) fs.reference = { path: p.reference_upload, url: p.reference_url, name: basename(p.reference_upload) }; else delete fs.reference; }
  if (job.kind === 'sprite') { if (p.image_upload) fs.image = { path: p.image_upload, url: p.image_url, name: basename(p.image_upload) }; else delete fs.image; (p.ref_uploads || []).forEach((r, i) => fs['ref' + i] = { path: r, url: (p.ref_urls || [])[i], name: basename(r) }); if (!(p.ref_uploads || [])[0]) delete fs.ref0; if (!(p.ref_uploads || [])[1]) delete fs.ref1; }
  fillForm(form, p);
  for (const u of form.querySelectorAll('.field')) if (u.refresh) u.refresh();
  form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  toast('settings loaded into the form');
}
function logBox(job) { const lg = h('div', { class: 'log', 'data-log': job.id }, (job.log || []).join('\n')); setTimeout(() => lg.scrollTop = lg.scrollHeight); return lg; }
function thumbStrip(items, onpick, selUrl) {
  const s = h('div', { class: 'thumbs' });
  for (const it of items) {
    const t = h('div', { class: 't' + (it.url === selUrl ? ' sel' : ''), title: it.label, onclick: () => onpick(it) }, it.kind === 'image' ? h('img', { src: it.url, loading: 'lazy', onerror: e => e.target.closest('.t').remove() }) : h('div', { class: 'm' }, it.kind === 'model' ? '⬡' : '📄'), h('span', {}, it.label));
    s.append(t);
  }
  return s;
}
async function mountViewer(container, url, onReady) {
  try {
    if (!viewerMod) viewerMod = await import('/viewer.js');
    const old = viewers.get(container); if (old) old.dispose();
    const v = new viewerMod.Viewer(container); viewers.set(container, v);
    const info = await v.load(url);
    if (onReady) onReady(v, info);
  } catch (e) { container.innerHTML = ''; container.append(h('div', { class: 'empty' }, '3D viewer unavailable (three.js from the CDN did not load): ', String(e.message || e))); }
}
function viewerBox(url, onReady) { const c = h('div', { class: 'viewer' }); setTimeout(() => mountViewer(c, url, onReady)); return c; }
function copyable(text) {
  if (!text) return null;
  return h('span', { class: 'copyrow' }, h('span', { class: 'copytext' }, text), h('button', { class: 'btn xs', title: 'Copy to clipboard', onclick: e => { navigator.clipboard.writeText(text).then(() => { e.target.textContent = '✓ copied'; setTimeout(() => e.target.textContent = 'Copy', 1500); }); } }, 'Copy'));
}
function kv(pairs) { const d = h('dl', { class: 'kv' }); for (const [k, v] of pairs) if (v != null && v !== '') d.append(h('dt', {}, k), h('dd', {}, v)); return d; }
function dl(url, label) { return url ? h('a', { class: 'btn sm', href: url, download: basename(url) }, '⤓ ' + label) : null; }
function reveal(path) { return path ? h('button', { class: 'btn sm', onclick: () => api.post('/api/reveal', { path }) }, '📂 Show in folder') : null; }

// ------------------------------------------------------------------ result views (activity card + library detail)
function resultView(job, lib) {
  // job: a job dict (may be null for untracked library items); lib: library item (may be null)
  const kind = job ? job.kind : lib.kind;
  if (kind === 'model3d' || kind === 'paint' || kind === 'prop') return result3d(job, lib);
  if (kind === 'texture') return resultTexture(job, lib);
  if (kind === 'sprite') return resultSprite(job, lib);
  if (kind === 'voice') return resultVoice(job, lib);
  if (kind === 'voice_prep') return resultVoicePrep(job);
  if (kind === 'music') return resultMusic(job, lib);
  if (kind === 'sfx') return resultSfx(job, lib);
  if (kind === 'video') return resultVideo(job, lib);
  if (kind === 'anim') return resultAnim(job, lib);
}
function result3d(job, lib) {
  const o = (job && job.outputs) || {};
  const glb = o.glb?.url || lib?.glb;
  const ref = o.reference?.url || lib?.reference;
  const diffuse = o.diffuse_fbx?.url || o.diffuse?.url || lib?.diffuse;
  const stage = job?.params?.stage;
  const box = h('div', {});
  if (job && stage === 'image' && job.status === 'done') {
    box.append(h('div', { class: 'preview-box', style: { minHeight: '0' } }, h('img', { src: ref, onclick: () => lightbox(ref), style: { cursor: 'zoom-in' } })),
      h('div', { class: 'actions' },
        h('button', { class: 'btn primary', onclick: () => submit('model3d', Object.assign({}, job.params, { stage: 'model' }), job.id) }, '▶ Build the 3D model from this image'),
        h('button', { class: 'btn', onclick: () => submit('model3d', Object.assign({}, job.params, { stage: 'image', seed: null })) }, '🎲 Another seed'),
        h('button', { class: 'btn', onclick: () => reuse(job) }, '✎ Edit prompt'),
        dl(ref, 'PNG')),
      h('div', { class: 'hint' }, 'The 3D stage re-uses this exact seed, so ComfyUI serves the cached picture and goes straight to the shape.'));
    return box;
  }
  const inter = (job?.intermediates || []);
  const gallery = [];
  if (ref) gallery.push({ label: 'Reference', kind: 'image', url: ref });
  if (diffuse) gallery.push({ label: 'Diffuse', kind: 'image', url: diffuse });
  for (const it of inter) if (!gallery.some(g => g.url === it.url)) gallery.push(it);
  if (glb) gallery.unshift({ label: 'Model', kind: 'model', url: glb });
  const big = h('div', { class: 'big' });
  const show = it => {
    for (const v of viewers.keys()) if (big.contains(v)) { viewers.get(v).dispose(); viewers.delete(v); }
    big.innerHTML = ''; big.classList.toggle('checker', it.kind === 'image');
    if (it.kind === 'model') big.append(viewerBox(it.url));
    else if (it.kind === 'image') big.append(h('img', { src: it.url, onclick: () => lightbox(it.url), style: { cursor: 'zoom-in' } }));
    else big.append(h('a', { href: it.url, target: '_blank' }, it.label));
    for (const t of strip.children) t.classList.toggle('sel', t.title === it.label);
  };
  const strip = thumbStrip(gallery, show);
  if (gallery.length) box.append(big, h('div', { style: { marginTop: '10px' } }, strip)); else box.append(h('div', { class: 'empty' }, 'No files yet'));
  if (gallery.length) setTimeout(() => show(gallery[0]));
  const m = job?.meta || lib?.job?.meta || {};
  const p = job?.params || lib?.job?.params || {};
  box.append(h('div', { class: 'actions' }, dl(glb, 'GLB'), dl(o.fbx?.url || lib?.fbx, 'FBX'), dl(diffuse, 'Diffuse PNG'), reveal(o.glb?.path || o.fbx?.path)));
  if (job?.kind === 'prop' || m.paint) {   // modelled by Claude, then painted (library items carry no kind)
    const pt = m.paint || {};
    box.append(h('div', { class: 'actions' }, o.script?.url ? h('a', { class: 'btn sm', href: o.script.url, target: '_blank' }, '{ } Blender script') : null));
    box.append(kv([['Prompt', copyable(p.prompt)], ['Claude', pt.notes], ['Modelling', m.model_rounds != null && `${m.model_rounds} check round${m.model_rounds === 1 ? '' : 's'}, ${fmtDur(m.model_seconds)}${m.claude_cost_usd ? ', $' + m.claude_cost_usd.toFixed(2) + ' of usage' : ''}`],
      ['Paint prompt', pt.prompt], ['Front only', pt.front], ['Other views', pt.plain], ['Locked parts', (pt.lock || []).join(', ')], ['Low views', pt.low ? 'yes' : null], ['Seed', p.seed],
      ['Camera fit', m.fit && `silhouette overlap ${m.fit.iou}, yaw ${Math.round(m.fit.yaw)}°, elevation ${Math.round(m.fit.elev)}°`],
      ['Mesh', m.tris && `${m.tris} tris`], ['FBX', o.fbx ? '✓ ' + basename(o.fbx.path) : null]]));
    return box;
  }
  if (job?.kind === 'paint' || p.model_url || p.model_upload) {   // a painted prop (library items carry no kind)
    box.append(kv([['Prompt', copyable(p.prompt)], ['Front only', p.front], ['Other views', p.plain], ['Locked parts', p.lock], ['Low views', p.low ? 'yes' : null], ['Seed', p.seed],
      ['Camera fit', m.fit && `silhouette overlap ${m.fit.iou}, yaw ${Math.round(m.fit.yaw)}°, elevation ${Math.round(m.fit.elev)}°`],
      ['Mesh', m.tris && `${m.tris} tris${m.seen != null ? ', views reached ' + Math.round(m.seen * 100) + '% of the surface' : ''}${m.denoise ? ', denoise ' + m.denoise : ''}`], ['FBX', o.fbx ? '✓ ' + basename(o.fbx.path) : null]]));
    return box;
  }
  box.append(kv([['Prompt', copyable(p.prompt)], ['Preset', p.preset], ['3D model', p.model], ['Seed', p.seed ?? m.seed], ['Shape', p.shape && `${p.shape}${m.tris ? ', ' + m.tris + ' tris' : ''}`], ['Edges', m.edges && `${m.edges}, remesh ${m.remesh}, normal map ${m.normalmap ? 'on (' + (m.normal_from || 'remesh') + ')' : 'off'}${m.texture_from ? ', colours from ' + m.texture_from : ''}${p.dissolve ? ', dissolve ' + p.dissolve + '°' : ''}`], ['Resolution', m.resolution && `shape ${m.resolution}, texture ${m.texture}`], ['Size', p.size && p.size + ' m'], ['FBX', m.fbx_error ? '✗ ' + m.fbx_error : (o.fbx ? '✓ ' + basename(o.fbx.path) : null)]]));
  return box;
}
function resultTexture(job, lib) {
  const url = job?.outputs?.texture?.url || lib?.url;
  const box = h('div', {});
  if (!url) return h('div', { class: 'empty' }, 'No texture yet');
  const big = h('div', { class: 'big' }, h('img', { src: url, onclick: () => lightbox(url), style: { cursor: 'zoom-in' } }));
  const tile = seg('tile', [['1', 'Single'], ['2', '2×2'], ['4', '4×4']], '1');
  tile.style.width = '220px';
  tile.addEventListener('change', () => {
    const n = Number(tile.dataset.value); const im = big.querySelector('img'); const ar = im && im.naturalWidth ? im.naturalWidth / im.naturalHeight : (big.dataset.ar || 1);
    big.dataset.ar = ar; big.innerHTML = ''; big.classList.toggle('tiled', n > 1);
    if (n > 1) { big.style.backgroundImage = `url("${url}")`; big.style.backgroundSize = `${100 / n}% ${100 / n}%`; big.style.aspectRatio = String(ar); big.style.minHeight = '0'; big.style.maxWidth = ar < 1 ? Math.round(640 * ar) + 'px' : ''; big.style.margin = '0 auto'; }
    else { big.style.backgroundImage = ''; big.style.aspectRatio = ''; big.style.minHeight = ''; big.style.maxWidth = ''; big.style.margin = ''; big.append(h('img', { src: url, onclick: () => lightbox(url), style: { cursor: 'zoom-in' } })); }
  });
  const p = job?.params || lib?.job?.params || {}, m = job?.meta || lib?.job?.meta || {};
  box.append(h('div', { class: 'row', style: { marginBottom: '8px' } }, h('span', { class: 'muted small' }, 'Repeat preview'), tile), big,
    h('div', { class: 'actions' }, dl(url, 'PNG'), reveal(job?.outputs?.texture?.path || lib?.path), h('button', { class: 'btn sm', title: 'Start a new texture from this one', onclick: () => useAsTextureRef({ path: job?.outputs?.texture?.path || lib?.path, url, name: basename(url) }) }, '↪ Use as reference')),
    kv([['Prompt', copyable(p.prompt ?? lib?.meta?.prompt)], ['Full prompt sent', !p.prompt && lib?.meta?.text !== lib?.meta?.prompt ? copyable(lib?.meta?.text) : null], ['Seed', p.seed ?? m.seed ?? lib?.meta?.seed], ['Size', m.width && `${m.width}×${m.height}`], ['Tiling', m.tile], ['Steps', m.steps], ['LoRA', m.lora > 0 ? m.lora : null], ['Reference', m.reference ? `strength ${m.strength}` : null]]));
  return box;
}
function useAsTextureRef(v) { const fs = state.forms.texture = state.forms.texture || {}; fs.reference = v; for (const u of $('#page-texture form').querySelectorAll('.field')) if (u.refresh) u.refresh(); $('#page-texture form').scrollIntoView({ behavior: 'smooth' }); toast('reference set'); }
function resultSprite(job, lib) {
  const url = job?.outputs?.sprite?.url || lib?.url, path = job?.outputs?.sprite?.path || lib?.path;
  if (!url) return h('div', { class: 'empty' }, 'No sprite yet');
  const p = job?.params || lib?.job?.params || {}, m = job?.meta || lib?.job?.meta || {};
  const inputs = [];
  if (p.image_upload) inputs.push({ label: 'Source (image 1)', url: p.image_url, path: p.image_upload });
  (p.ref_uploads || []).forEach((r, i) => inputs.push({ label: `Reference (image ${i + 2})`, url: (p.ref_urls || [])[i], path: r }));
  const box = h('div', {},
    h('div', { class: 'split' },
      h('div', { class: 'big checker' }, h('img', { src: url, onclick: () => lightbox(url), style: { cursor: 'zoom-in' } })),
      h('div', {}, inputs.length ? h('div', {}, h('h3', {}, 'Inputs'), h('div', { class: 'thumbs', style: { marginTop: '8px' } }, inputs.map(i => h('div', { class: 't', title: i.label, onclick: () => lightbox(i.url) }, h('img', { src: i.url }), h('span', {}, i.label))))) : null,
        job?.outputs?.raw ? h('div', { style: { marginTop: '10px' } }, h('h3', {}, 'Before cut-out'), h('div', { class: 'thumbs', style: { marginTop: '8px' } }, h('div', { class: 't', onclick: () => lightbox(job.outputs.raw.url) }, h('img', { src: job.outputs.raw.url }), h('span', {}, 'raw')))) : null,
        kv([['Prompt', copyable(p.prompt ?? lib?.meta?.prompt)], ['Model', m.model], ['Seed', p.seed ?? m.seed ?? lib?.meta?.seed], ['Steps', m.steps && `${m.steps}${m.cfg ? ', cfg ' + m.cfg : ''}`], ['Canvas', m.size], ['Alpha', m.alpha]]))),
    h('div', { class: 'actions' }, dl(url, 'PNG'), reveal(path),
      h('button', { class: 'btn sm primary', onclick: () => setSpriteInput('image', { path, url, name: basename(url) }) }, '↪ Use as source'),
      h('button', { class: 'btn sm', onclick: () => setSpriteInput(state.forms.sprite?.ref0 ? 'ref1' : 'ref0', { path, url, name: basename(url) }) }, '＋ Add as reference')));
  return box;
}
function setSpriteInput(key, v) { const fs = state.forms.sprite = state.forms.sprite || {}; fs[key] = v; for (const u of $('#page-sprite form').querySelectorAll('.field')) if (u.refresh) u.refresh(); $('#page-sprite form').scrollIntoView({ behavior: 'smooth' }); toast(key === 'image' ? 'source sprite set' : 'reference added'); }
function resultVoice(job, lib) {
  const files = job?.outputs?.files || (lib ? [lib] : []);
  if (!files.length) return h('div', { class: 'empty' }, 'No audio yet');
  const p = job?.params || lib?.job?.params || {};
  const box = h('div', {}, h('div', { class: 'muted', style: { marginBottom: '8px' } }, '“', p.text || lib?.text || '', '”'));
  const tbl = h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Take'), h('th', {}, 'Audio'), h('th', {}, 'Seed'), h('th', {}, 'Length'), h('th', {}, ''))));
  files.forEach((f, i) => tbl.append(h('tr', {}, h('td', {}, files.length > 1 ? `#${i + 1}` : basename(f.url)), h('td', {}, h('audio', { controls: true, preload: 'none', src: f.url })), h('td', { class: 'mono' }, f.seed ?? ''), h('td', {}, f.duration ? f.duration + ' s' : ''), h('td', {}, dl(f.url, 'WAV'), ' ', reveal(f.path)))));
  box.append(tbl, kv([['Voice', (p.voice || lib?.voice) + (p.mood ? ` · ${p.mood}` : '')], ['Language', p.language], ['Temperature', p.temperature], ['Model', p.model ? basename(p.model) : null]]));
  return box;
}

// ------------------------------------------------------------------ activity card
const activity = {};
function activityCard(kind) {
  const card = h('div', { class: 'card activity' });
  const job = state.focus[kind] ? state.jobs.get(state.focus[kind]) : null;
  const queued = jobsOf(kind).filter(j => j.status === 'queued' && j.id !== job?.id);
  if (!job) { card.append(h('div', { class: 'empty' }, `Nothing generated yet. Fill in the form and press Generate.`)); return card; }
  const head = h('div', { class: 'card-head' }, h('span', { class: 'badge ' + job.status }, job.status), h('div', { class: 'title grow' }, job.title), h('span', { class: 'dim small' }, fmtDate(job.created)), ...jobActions(job));
  const pb = progressBlock(job);
  card.append(head, pb);
  if (job.error) card.append(h('div', { class: 'errbox' }, job.error));
  if (job.status === 'running' || job.status === 'queued') {
    const live = h('div', { class: 'live' },
      h('div', {}, h('h3', {}, 'Live preview'), h('div', { class: 'preview-box' + (AUDIO_KINDS.includes(kind) ? ' hidden' : ''), style: { marginTop: '8px' } }, h('span', { class: 'tag' }, 'sampler preview'), h('img', { 'data-preview': job.id, src: `/api/jobs/${job.id}/preview?seq=${job.preview_seq || 0}`, onerror: e => { e.target.parentElement.classList.add('hidden'); } }))),
      h('div', {}, h('h3', {}, 'Intermediate results'), h('div', { class: 'inter', style: { marginTop: '8px' } }, job.intermediates?.length ? thumbStrip(job.intermediates, it => it.kind === 'image' ? lightbox(it.url) : modal(h('div', { style: { width: '80vw' } }, viewerBox(it.url)))) : h('div', { class: 'dim small' }, kind === 'voice' ? 'The worker reports when the model is loaded and when the line is generated.' : kind === 'music' ? 'The planner LM runs first (bpm, key, structure, semantic codes), then the DiT diffuses 8 steps and the VAE decodes.' : kind === 'sfx' ? 'The text encoder reads the prompt, the DiT runs its flow-matching steps (two passes each for guidance), then the DAC VAE decodes 48 kHz audio.' : kind === 'video' ? 'Keyframes and each shot\'s last frame appear here as they finish; the sampler preview shows the clip being painted.' : 'Images and meshes appear here as each node finishes.'))));
    card.append(live);
    if (AUDIO_KINDS.includes(kind)) live.firstChild.classList.add('hidden');
  } else if (job.status === 'done') {
    card.append(h('div', { style: { marginTop: '12px' } }, resultView(job, null)));
  }
  card.append(h('details', { style: { marginTop: '10px' } }, h('summary', { class: 'dim small', style: { cursor: 'pointer' } }, 'Log'), logBox(job)));
  if (queued.length) card.append(h('div', { class: 'dim small', style: { marginTop: '8px' } }, `${queued.length} more ${kind === 'voice' ? 'line' : kind === 'music' ? 'track' : kind === 'sfx' ? 'sound' : kind === 'video' ? 'video' : 'job'}${queued.length > 1 ? 's' : ''} queued: `, queued.map(j => j.title.slice(0, 40)).join(' · ')));
  card.update = j => { pb.update(j); const b = head.querySelector('.badge'); if (b.textContent !== j.status) { b.textContent = j.status; b.className = 'badge ' + j.status; } };
  return card;
}
function updateActivity(kind, host) {
  const job = state.focus[kind] ? state.jobs.get(state.focus[kind]) : null;
  const cur = activity[kind];
  const sig = job ? `${job.id}:${job.status}:${(job.intermediates || []).length}:${job.cancel_requested}` : 'none';
  if (cur && cur.sig === sig && host.contains(cur.el)) { if (job) cur.el.update(job); return; }
  const el = activityCard(kind);
  if (cur && host.contains(cur.el)) { for (const v of viewers.keys()) if (cur.el.contains(v)) { viewers.get(v).dispose(); viewers.delete(v); } cur.el.replaceWith(el); } else host.prepend(el);
  activity[kind] = { sig, el };
}

// ------------------------------------------------------------------ library
function libraryCard(kind, it) {
  const key = it.path || it.name;
  const sel = state.selected[kind] === key;
  const img = kind === 'model3d' ? (it.thumb || it.reference || it.compare || it.diffuse) : kind === 'anim' ? (it.thumb || it.diffuse) : it.url;
  const sub = kind === 'voice' ? `${it.voice || ''}${it.mood ? ' · ' + it.mood : ''} · ${fmtDate(it.mtime)}` : kind === 'anim' ? `${(it.clips || []).length} clip${(it.clips || []).length === 1 ? '' : 's'} · ${fmtDate(it.mtime)}` : fmtDate(it.mtime);
  return h('div', { class: 'tile' + (sel ? ' sel' : ''), onclick: () => { state.selected[kind] = key; renderFlow(kind); } },
    h('div', { class: 'img' + (kind === 'texture' ? ' tex' : ''), 'data-thumb-for': kind === 'model3d' ? it.name : kind === 'anim' ? it.thumbName : null }, img ? h('img', { src: img, loading: 'lazy' }) : h('span', { class: 'ph' }, kind === 'model3d' ? '⬡' : kind === 'anim' ? '🏃' : '♪')),
    h('div', { class: 'cap' }, h('b', { title: it.name }, it.name), h('span', {}, sub, it.untracked ? ' · raw' : '')));
}
function libraryBlock(kind) {
  const items = state.library[kind] || [];
  const wrap = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Library'), h('span', { class: 'dim small' }, `${items.length} ${kind === 'voice' ? 'takes' : kind === 'music' ? 'tracks' : kind === 'sfx' ? 'sounds' : 'items'}`), h('button', { class: 'btn xs right', onclick: () => loadLibrary(kind, true) }, '↻ Rescan')));
  if (!items.length) { wrap.append(h('div', { class: 'empty' }, 'Nothing here yet')); return wrap; }
  if (kind === 'voice') { wrap.append(voiceTable(items)); return wrap; }
  if (kind === 'music') { wrap.append(musicTable(items)); return wrap; }
  if (kind === 'sfx') { wrap.append(sfxTable(items)); return wrap; }
  if (kind === 'video') { wrap.append(videoTable(items)); return wrap; }
  const grid = h('div', { class: 'grid' }); for (const it of items) grid.append(libraryCard(kind, it)); wrap.append(grid);
  if (kind === 'model3d' || kind === 'anim') queueThumbs(items.filter(it => it.glb && !it.thumb));
  const key = state.selected[kind]; const it = items.find(i => (i.path || i.name) === key);
  if (it) {
    const job = it.job ? Object.assign({ kind, status: 'done', id: it.job.id }, it.job) : null;
    const det = h('div', { class: 'detail' }, h('div', { class: 'card-head' }, h('h2', {}, it.name), h('span', { class: 'dim small' }, fmtDate(it.mtime)), job ? h('button', { class: 'btn xs', onclick: () => reuse(job) }, '↻ Reuse settings') : null, h('button', { class: 'btn xs right', onclick: () => { delete state.selected[kind]; renderFlow(kind); } }, '✕')),
      resultView(job, Object.assign({ kind }, it)));
    wrap.append(det);
  }
  return wrap;
}
function voiceTable(items) {
  const tbl = h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, 'Date'), h('th', {}, 'Voice'), h('th', {}, 'Line'), h('th', {}, 'Length'), h('th', {}, 'Seed'), h('th', {}, ''))));
  for (const it of items) {
    const job = it.job ? Object.assign({ kind: 'voice', status: 'done', id: it.job.id }, it.job) : null;
    tbl.append(h('tr', {}, h('td', {}, playBtn(it.url)), h('td', { class: 'small', style: { whiteSpace: 'nowrap' } }, fmtDate(it.mtime)),
      h('td', {}, h('b', { style: { textTransform: 'capitalize' } }, it.voice || ''), it.mood ? h('span', { class: 'dim' }, ' · ' + it.mood) : null),
      h('td', {}, h('span', { class: 'text', title: it.text || it.rel }, it.text || it.rel)), h('td', { class: 'small' }, it.duration ? it.duration + ' s' : ''), h('td', { class: 'mono' }, it.seed ?? ''),
      h('td', { style: { whiteSpace: 'nowrap' } }, job ? h('button', { class: 'btn xs', title: 'Load this line and its settings into the form', onclick: () => reuse(job) }, '↻') : null, ' ', dl(it.url, 'WAV'))));
  }
  return tbl;
}
// 3D thumbnails: rendered in the browser with an off-screen viewer, cached by the server in studio/thumbs
const thumbQueue = []; let thumbBusy = false; const thumbFailed = new Set();
function queueThumbs(items) { for (const it of items) if (!thumbQueue.includes(it) && !thumbFailed.has(it.thumbName || it.name)) thumbQueue.push(it); runThumbs(); }
async function runThumbs() {
  if (thumbBusy || !thumbQueue.length) return;
  thumbBusy = true;
  const it = thumbQueue.shift();
  const box = h('div', { class: 'viewer', style: { position: 'fixed', left: '-2000px', top: '0', width: '320px', height: '320px' } });
  document.body.append(box);
  try {
    if (!viewerMod) viewerMod = await import('/viewer.js');
    const v = new viewerMod.Viewer(box);
    await v.load(it.glb);
    v.fit();
    const dataUrl = v.snapshot(320);
    v.dispose();
    const blob = await (await fetch(dataUrl)).blob();
    const tn = it.thumbName || it.name;
    const r = await fetch('/api/thumb?name=' + encodeURIComponent(tn), { method: 'POST', body: blob });
    const j = await r.json();
    if (j.url) { it.thumb = j.url; for (const el of document.querySelectorAll(`[data-thumb-for="${CSS.escape(tn)}"]`)) { el.innerHTML = ''; el.append(h('img', { src: j.url })); } }
  } catch (e) { thumbFailed.add(it.thumbName || it.name); console.warn('thumbnail failed for', it.name, e); }
  box.remove();
  thumbBusy = false;
  runThumbs();
}
let currentAudio = null;
function playBtn(url) {
  const b = h('button', { class: 'play', title: 'Play' }, '▶');
  b.onclick = () => {
    if (currentAudio && currentAudio.src.endsWith(url)) { currentAudio.pause(); currentAudio = null; b.classList.remove('on'); b.textContent = '▶'; return; }
    if (currentAudio) { currentAudio.pause(); document.querySelectorAll('.play.on').forEach(x => { x.classList.remove('on'); x.textContent = '▶'; }); }
    currentAudio = new Audio(url); currentAudio.play(); b.classList.add('on'); b.textContent = '■';
    currentAudio.onended = () => { b.classList.remove('on'); b.textContent = '▶'; currentAudio = null; };
  };
  return b;
}

// ------------------------------------------------------------------ flow pages
const built = {};
function renderFlow(kind) {
  const page = $(`#page-${kind}`);
  if (!built[kind]) {
    page.innerHTML = ''; page.append(h('div', { class: 'layout' }, h('div', { class: 'formcol' }, buildForm(kind)), h('div', { class: 'maincol stack' }))); built[kind] = true;
    // the data-driven forms may already have their data (a job finished before the page was first opened and
    // refreshed it); loadLibrary() will not fetch again, so build the form from what is there
    const late = { voice: () => state.voices.length && buildVoiceForm(), music: () => state.music && buildMusicForm(), sfx: () => state.sfx && buildSfxForm(), video: () => state.video && buildVideoForm(), anim: () => state.anim && buildAnimForm() }[kind];
    if (late) late();
  }
  const main = $('.maincol', page);
  updateActivity(kind, main);
  const old = $('.maincol > .card:not(.activity)', page);
  if (state.libLoaded[kind]) { const lb = libraryBlock(kind); if (old) { for (const v of viewers.keys()) if (old.contains(v)) { viewers.get(v).dispose(); viewers.delete(v); } old.replaceWith(lb); } else main.append(lb); }
}
function buildForm(kind) { return { model3d: build3dForm, texture: buildTextureForm, sprite: buildSpriteForm, voice: buildVoiceFormShell, music: buildMusicFormShell, sfx: buildSfxFormShell, video: buildVideoFormShell, anim: buildAnimFormShell }[kind](); }
async function submit(kind, params, parent) {
  try {
    const job = await api.post('/api/jobs', { kind, params, parent });
    state.jobs.set(job.id, job); state.focus[pageOf(kind)] = job.id; scheduleRender();
    toast(state.system?.running ? 'queued (one job runs at a time)' : 'started');
  } catch (e) { toast(e.message, 'err'); }
}

function build3dForm() {
  const fs = state.forms.model3d = state.forms.model3d || {};
  const form = h('form', { class: 'card form', id: 'model3d-form', onsubmit: e => e.preventDefault() });
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Text → 3D prop')),
    field('Prompt', h('textarea', { name: 'prompt', placeholder: 'an office desk chair with wheels' }), 'One object, not a scene. The preset appends its own style hints (low-poly, hand-painted, plain background).'),
    h('div', { class: 'two' }, field('Asset name', text('name', '', { placeholder: 'DeskChair (from prompt)' })), field('Size (m)', num('size', 1.0, { step: 0.1, min: 0.05 }))),
    field('Preset', segf('preset', [['gameasset', 'Game asset', 'Low-poly prop: 3000 tris decimated then planar-dissolved (box ~1200, chair ~2100), 1024 diffuse + normal map, FBX for Unity'], ['character', 'Character', 'A person or creature in a front-view T-pose, ~4000 tris: rig and animate it on the Animate page'], ['detailed', 'Detailed', 'Hero model: 700k tris, 4K PBR maps, GLB only']], 'gameasset')),
    field('Shape', segf('shape', [['hard', 'Hard edges', 'furniture, boxes, machines'], ['round', 'Round', 'bottles, pots, balls: fully smooth, 1500 tris, no dissolve']], 'hard')),
    field('Edges', segf('edges', [['sharp', 'Sharp', 'corners and creases survive remesh + decimation (QEF / QEM with pinned feature edges)'], ['soft', 'Soft (old)', '20 smoothing passes + midpoint collapses: rounds everything off']], 'sharp'),
      'Sharp is the fix for the melted look; Soft is what the pipeline did before and is kept for comparison.'),
    presetInfoBlock(form),
    h('div', { class: 'two' }, field('Triangle budget', num('tris', '', { placeholder: '3000 / 1500', step: 100, min: 100 })), field('3D model', select('model', [['pixal3d', 'Pixal3D'], ['trellis2', 'TRELLIS.2']], 'pixal3d'))),
    seedField(),
    uploadBox('model3d', 'reference', 'Own reference image (optional)', { hint: 'Skips text-to-image: Pixal3D works straight from your picture' }),
    h('details', {}, h('summary', {}, 'Advanced'), h('div', { class: 'two' }, field('Shape resolution', num('resolution', '', { placeholder: 'preset', step: 256 })), field('Texture size', num('texture', '', { placeholder: 'preset', step: 512 }))),
      field('Remesh resolution', num('remesh', '', { placeholder: '512 (round 384)', step: 64, min: 64, max: 1024 }), 'Voxel grid of the remesh before decimation. 512 keeps taps, ribs and collars separate where 256 melts them; set 256 for perforated parts like a mesh chair back, where 512 resolves the holes and the decimator stalls on them.'),
      field('Diffuse colours from', select('texture_from', [['picture', 'Reference picture (crisp, Pixal3D)'], ['voxels', 'Voxel bake (soft)']], 'picture'), 'Pixal3D builds the object exactly where it is in the reference picture, so the picture is projected onto the faces the camera saw; the voxel bake fills the hidden side, colour-matched to the picture.'),
      h('div', { class: 'two' }, field('Diffuse blur (px)', num('blur', '', { placeholder: '0 (round 2)', step: 1, min: 0, max: 8 })), field('AO in diffuse', num('ao', '', { placeholder: '0.3 (round 0.45)', step: 0.05, min: 0, max: 1 }))),
      check('normalmap', 'Bake a normal map from the high-poly (bevels and panel lines shade as if modelled; also written as <name>Normal.png for Unity)', true),
      h('div', { class: 'two' },
        field('Normal map from', select('normal_from', [['remesh', 'Remeshed shell (smooth)'], ['raw', 'Raw VAE mesh (finest detail, voxel grain)']], 'remesh')),
        field('Planar dissolve (°)', num('dissolve', '', { placeholder: '10 (round 0)', step: 1, min: 0, max: 30 }), 'After decimation Blender merges coplanar triangles within this angle (inside UV islands, textures stay valid): flat panels give triangles back, so a bigger budget goes to small parts. Needs FBX.')),
      check('fbx', 'Convert to FBX + diffuse PNG with Blender (game asset)', true), check('raw', 'Raw prompt: no style hints', false)),
    h('div', { class: 'actions' },
      h('button', { class: 'btn primary block', type: 'button', onclick: () => run('image') }, '1 · Reference image only'),
      h('button', { class: 'btn block', type: 'button', onclick: () => run('full') }, '▶ Full run: image → 3D → FBX')),
    h('div', { class: 'hint' }, 'The reference image takes ~20 s; the 3D stage ~3 min (game asset) to 25 min (detailed). Generate the image first, check it, then build the model from it.'));
  const run = stage => {
    const p = readForm(form); p.stage = stage;
    if (!p.prompt.trim() && !fs.reference) return toast('write a prompt (or give a reference image)', 'err');
    if (fs.reference) { p.reference_upload = fs.reference.path; p.reference_url = fs.reference.url; if (stage === 'image') return toast('with your own reference image there is no image stage: use the full run', 'err'); }
    submit('model3d', p);
  };
  return flowChooser(form, buildPropForm(), buildPaintForm());
}
// the 3D page has three flows: pick one with a large button, each form has a back button to the choice
const FLOWS_3D = [
  { key: 'model3d', icon: '⬡', title: 'Text → 3D prop', sub: 'Generated mesh: Pixal3D builds it from a picture, decimated to a low-poly game asset.', time: '~3 min' },
  { key: 'prop', icon: '✎', title: 'Text → modelled prop', sub: 'Claude models it in Blender the way an artist box-models it, then it is painted from the picture.', time: '~5–20 min' },
  { key: 'paint', icon: '🖌', title: 'Paint a modelled prop', sub: 'You already have the model (a GLB from Blender) and its reference picture: paint it.', time: '~2–3 min' },
];
function show3dFlow(key) {
  const host = $('#flow3d'); if (!host) return;
  host.dataset.flow = key || '';
  try { if (key) localStorage.setItem('flow3d', key); else localStorage.removeItem('flow3d'); } catch (e) { /* private window: the choice lasts for this page only */ }
  for (const el of host.children) el.classList.toggle('hidden', key ? el.dataset.flow !== key : el.dataset.flow !== 'pick');
}
function flowChooser(...forms) {
  const pick = h('div', { class: 'card flow-pick', 'data-flow': 'pick' }, h('div', { class: 'card-head' }, h('h2', {}, 'What do you want to make?')),
    ...FLOWS_3D.map(f => h('button', { type: 'button', class: 'flow-btn', onclick: () => show3dFlow(f.key) },
      h('span', { class: 'flow-ico' }, f.icon), h('span', { class: 'flow-txt' }, h('b', {}, f.title), h('span', {}, f.sub)), h('span', { class: 'flow-time' }, f.time))));
  const keys = ['model3d', 'prop', 'paint'];
  forms.forEach((form, i) => {
    form.dataset.flow = keys[i];
    form.querySelector('.card-head').prepend(h('button', { type: 'button', class: 'btn sm', title: 'Back to the choice of flow', onclick: () => show3dFlow(null) }, '← Back'));
  });
  const host = h('div', { id: 'flow3d' }, pick, ...forms);
  let last = null; try { last = localStorage.getItem('flow3d'); } catch (e) { /* no storage */ }
  setTimeout(() => show3dFlow(keys.includes(last) ? last : null));
  return host;
}
function pickReference(cb) {
  // a reference picture from the 3D library (what a prop was modelled from)
  loadLibrary('model3d').then(() => {
    const items = (state.library.model3d || []).filter(it => it.reference);
    const grid = h('div', { class: 'grid' });
    for (const it of items) grid.append(h('div', { class: 'tile', onclick: () => { cb({ url: it.reference, name: it.name + ' reference' }); $('#modal').classList.add('hidden'); } },
      h('div', { class: 'img' }, h('img', { src: it.reference, loading: 'lazy' })), h('div', { class: 'cap' }, h('b', {}, it.name), h('span', {}, fmtDate(it.mtime)))));
    modal(h('div', { style: { width: '70vw', maxHeight: '75vh', overflow: 'auto' } }, h('h3', { style: { marginBottom: '10px' } }, 'Pick the reference picture'), items.length ? grid : h('div', { class: 'empty' }, 'No reference pictures in the 3D library yet: make one with "1 · Reference image only" above')));
  });
}
function buildPropForm() {
  const fs = state.forms.prop = state.forms.prop || {};
  const form = h('form', { class: 'card form', id: 'prop-form', onsubmit: e => e.preventDefault() });
  const warn = h('div', {});
  const showReady = P => { warn.innerHTML = ''; const r = P && P._paint; const miss = []; if (r && !r.ready) miss.push('painting (run ai-3d\\setup_paint.ps1, then restart ComfyUI)'); if (P && P._claude === false) miss.push('the Claude Code CLI (install Claude Code and log in)'); if (miss.length) warn.append(h('div', { class: 'errbox' }, 'Not set up: ' + miss.join('; ') + '.')); };
  if (state.presets3d) showReady(state.presets3d); else api.get('/api/presets3d').then(P => { state.presets3d = P; showReady(P); }).catch(() => {});
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Text → modelled prop (Claude)')),
    h('div', { class: 'hint' }, 'Z-Image draws the reference picture, Claude models the prop in Blender the way an artist box-models it (checking clay renders against the picture each round), then it is painted from the picture. Best for hard-surface props: furniture, vehicles, tools, containers, signs.'),
    warn,
    field('Prompt', h('textarea', { name: 'prompt', placeholder: 'a safety orange and black snowmobile with a windshield and two front skis' }), 'One object, not a scene.'),
    h('div', { class: 'two' }, field('Asset name', text('name', '', { placeholder: 'Snowmobile (from prompt)' })), field('Claude model', select('claude_model', [['opus', 'Opus'], ['sonnet', 'Sonnet'], ['', 'Claude Code default']], 'opus'))),
    seedField(),
    uploadBox('prop', 'reference', 'Own reference picture (optional)', { hint: 'Skips drawing one: Claude models from your picture', pick: pickReference }),
    h('details', {}, h('summary', {}, 'Advanced'),
      h('div', { class: 'two' }, field('Check rounds (max)', num('rounds', 6, { step: 1, min: 1, max: 12 }), 'Build + compare rounds Claude may take.'), field('Effort', select('effort', [['', 'default'], ['medium', 'medium'], ['high', 'high'], ['xhigh', 'xhigh']], ''))),
      h('div', { class: 'two' }, field('IP-Adapter strength', num('ipadapter', 0.6, { step: 0.05, min: 0, max: 1.5 })), field('Denoise', num('denoise', '', { placeholder: '0.7', step: 0.05, min: 0.2, max: 1 }))),
      h('div', { class: 'two' }, field('Diffuse size', select('resolution', [512, 1024, 2048], 1024)), field('Size (m)', num('size', '', { placeholder: 'real size', step: 0.1, min: 0.01 }))),
      check('fbx', 'Also write the Unity FBX + <Name>Diffuse.png', true), check('raw', 'Raw prompt for the picture: no style hints', false)),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary block', type: 'button', onclick: () => run() }, '▶ Picture → Claude models → paint')),
    h('div', { class: 'hint' }, 'About 10–20 minutes, mostly Claude modelling (it uses your Claude Code login and usage). The clay check of each round shows up as it goes, then each painted view.'));
  const run = () => {
    const p = readForm(form);
    if (!p.prompt.trim()) return toast('describe the prop', 'err');
    if (fs.reference) { if (fs.reference.path) p.reference_upload = fs.reference.path; else p.reference_url = fs.reference.url; p.reference_name = fs.reference.name; }
    p.resolution = Number(p.resolution);
    submit('prop', p);
  };
  return form;
}
function buildPaintForm() {
  const fs = state.forms.paint = state.forms.paint || {};
  const form = h('form', { class: 'card form', id: 'paint-form', onsubmit: e => e.preventDefault() });
  const warn = h('div', {});
  const showReady = P => { const r = P && P._paint; warn.innerHTML = ''; if (r && !r.ready) warn.append(h('div', { class: 'errbox' }, 'Painting is not set up (' + Object.keys(r).filter(k => k !== 'ready' && !r[k]).join(', ') + ' missing): run ai-3d\\setup_paint.ps1, then restart ComfyUI.')); };
  if (state.presets3d) showReady(state.presets3d); else api.get('/api/presets3d').then(P => { state.presets3d = P; showReady(P); }).catch(() => {});
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Paint a modelled prop')),
    h('div', { class: 'hint' }, 'For props modelled in Blender, by hand or by Claude with ai-3d/scripts/prop_kit.py (front facing -Y, one named material per part). SDXL repaints a render of the model in its part colours from every side (depth ControlNet, the picture as IP-Adapter), so the views agree; they are projected and baked into one diffuse.'),
    warn,
    modelBox(fs, 'model', 'Modelled prop (GLB)', 'Pick the modelled prop to paint'),
    uploadBox('paint', 'picture', 'Reference picture', { hint: 'The picture the prop was modelled from', pick: pickReference }),
    field('Prompt', h('textarea', { name: 'prompt', placeholder: 'stylized 3D game prop, soft hand-painted texture, clean, pastel, wooden office desk with a pedestal' }), 'The whole prop and its materials; sent to every view.'),
    field('Front only', text('front', '', { placeholder: 'drawers with metal handles on the front' }), "Details only the picture's side has (a logo, drawers): kept out of the other views so they are not repeated all round."),
    field('Other views', text('plain', '', { placeholder: 'plain flat wooden back panel, no drawers' }), 'How every other view looks, for the whole prop: a top view told only "plain cardboard sleeve" paints the lid as cardboard.'),
    h('div', { class: 'two' }, field('Asset name', text('name', '', { placeholder: 'from the GLB' })), field('Lock parts', text('lock', '', { placeholder: 'Snow,Floor' }), 'Material names of plain parts: they keep the shading but one colour, so bleed from a neighbour is removed.')),
    check('low', 'Low views: paint the underside too (chairs, tables). Off for other props: there they invent a second logo', false),
    seedField(),
    h('details', {}, h('summary', {}, 'Advanced'),
      h('div', { class: 'two' }, field('IP-Adapter strength', num('ipadapter', 0.6, { step: 0.05, min: 0, max: 1.5 }), 'How much the picture styles every view.'), field('Denoise', num('denoise', '', { placeholder: '0.7', step: 0.05, min: 0.2, max: 1 }), 'How far each view may move from the render of the part colours: lower keeps the modelled colours, higher adds detail. Default 0.7 (0.85 for a single-material model).')),
      h('div', { class: 'two' }, field('Diffuse size', select('resolution', [512, 1024, 2048], 1024)), field('Size (m)', num('size', '', { placeholder: 'as modelled', step: 0.1, min: 0.01 }))),
      check('fbx', 'Also write the Unity FBX + <Name>Diffuse.png', true)),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary block', type: 'button', onclick: () => run() }, '🖌 Paint')),
    h('div', { class: 'hint' }, 'About 2–3 minutes. Give the parts their colours in the model (one named material per part): every view starts from them.'));
  const run = () => {
    const p = readForm(form);
    if (!fs.model) return toast('pick the modelled GLB (3D library or a file)', 'err');
    if (!fs.picture) return toast('pick the reference picture it was modelled from', 'err');
    if (!p.prompt.trim()) return toast('describe the prop in the prompt', 'err');
    if (fs.model.path) p.model_upload = fs.model.path; else p.model_url = fs.model.url;
    p.model_name = fs.model.name;
    if (fs.picture.path) p.picture_upload = fs.picture.path; else p.picture_url = fs.picture.url;
    p.picture_name = fs.picture.name;
    p.resolution = Number(p.resolution);
    submit('paint', p);
  };
  return form;
}
function presetInfoBlock(form) {
  // what the chosen preset appends to the prompt and which settings it fixes, plus the complete prompt as sent
  const body = h('div', { class: 'preset-info' }, h('div', { class: 'dim small' }, 'loading preset details…'));
  const det = h('details', { class: 'preset-details', open: localStorage.getItem('presetInfoOpen') === '1', ontoggle: e => localStorage.setItem('presetInfoOpen', e.target.open ? '1' : '0') },
    h('summary', {}, 'What the preset does · complete prompt'), body);
  const refresh = () => {
    const P = state.presets3d; if (!P) return;
    const p = readForm(form); const pr = P[p.preset]; if (!pr) return;
    const prompt = (p.prompt || '').trim();
    const full = p.raw ? prompt : prompt + pr.style;
    const tris = p.tris || (p.shape === 'round' && pr.tris_round ? pr.tris_round : pr.tris);
    body.innerHTML = '';
    body.append(
      h('div', { class: 'lbl' }, p.raw ? 'Style hints (not appended: Raw prompt is on)' : 'Appended to your prompt'),
      h('div', { class: 'promptbox' + (p.raw ? ' off' : '') }, pr.style.replace(/^,\s*/, '')),
      h('div', { class: 'lbl' }, 'Settings the preset fixes'),
      kv([['Workflow', pr.workflow], ['Shape resolution', (p.resolution || pr.resolution) + (p.resolution ? ' (yours)' : '')], ['Texture', (p.texture || pr.texture) + (p.texture ? ' (yours)' : '')],
        ['Triangles', pr.decimate ? `${tris}${p.tris ? ' (yours)' : p.shape === 'round' ? ' (round default)' : ' (default)'}` : 'no decimation (~700k)'],
        ['Shape', pr.decimate ? (p.shape === 'round' ? P._shape_round : `Hard: crease angle 60°, remesh ${p.remesh || 512}, planar dissolve ${p.dissolve ?? pr.dissolve}°`) : 'n/a'],
        ['Pipeline', pr.notes], ['Output', pr.fbx ? 'GLB + FBX + diffuse PNG' : 'GLB with PBR maps']]),
      h('div', { class: 'lbl' }, 'Complete prompt sent to Z-Image-Turbo'),
      h('div', { class: 'promptbox full' }, full || '(empty: type a prompt above)'),
      full ? h('button', { type: 'button', class: 'btn xs', style: { marginTop: '6px' }, onclick: e => { navigator.clipboard.writeText(full).then(() => { e.target.textContent = '✓ copied'; setTimeout(() => e.target.textContent = 'Copy the complete prompt', 1500); }); } }, 'Copy the complete prompt') : null);
    const trisInput = form.querySelector('input[name="tris"]');
    if (trisInput) trisInput.placeholder = pr.decimate ? String(p.shape === 'round' && pr.tris_round ? pr.tris_round : pr.tris) : 'no decimation';
  };
  form.addEventListener('input', refresh);
  form.addEventListener('change', refresh);
  if (state.presets3d) refresh(); else api.get('/api/presets3d').then(P => { state.presets3d = P; refresh(); }).catch(e => { body.textContent = 'preset details unavailable: ' + e.message; });
  return det;
}
function buildTextureForm() {
  const fs = state.forms.texture = state.forms.texture || {};
  const form = h('form', { class: 'card form', onsubmit: e => e.preventDefault() });
  const sizes = [256, 512, 768, 1024, 1536, 2048];
  const cross = h('div', { class: 'two hidden' }, field('Seam band (px)', num('band', 192, { step: 32 })), field('Repaint denoise', num('denoise', 0, { step: 0.1, min: 0, max: 1 })));
  const tile = segf('tile', [['native', 'Native', 'Seamless by construction: plank rows and tile grids line up'], ['crossfade', 'Cross-fade', 'For flat walls: no faint roll lines'], ['none', 'None', 'Plain picture']], 'native');
  tile.addEventListener('change', () => cross.classList.toggle('hidden', tile.dataset.value !== 'crossfade'));
  const strengthRow = h('div', { class: 'hidden' }, field('Reference strength', h('input', { type: 'range', name: 'strength', min: 0.2, max: 1, step: 0.05, value: 0.6 }), 'How far the result may drift from the reference: 0.3 keeps the layout, 0.8 only keeps the palette.'));
  const refBox = uploadBox('texture', 'reference', 'Reference texture (optional)', { hint: 'Start from an existing texture instead of noise' });
  const obs = new MutationObserver(() => strengthRow.classList.toggle('hidden', !fs.reference)); obs.observe(refBox, { childList: true, subtree: true });
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Text → seamless texture')),
    field('Material', h('textarea', { name: 'prompt', placeholder: 'dark oak parquet floor, long horizontal planks in staggered rows with thin dark seams, warm orange-brown wood' }), 'Name the material, colour and layout. Avoid “outlines”/“seams” in general terms (it draws a grid); say “thin dark seams between planks”.'),
    field('Name', text('name', '', { placeholder: 'ParquetFloorDark (from prompt)' })),
    h('div', { class: 'two' }, field('Width', select('width', sizes, 1024)), field('Height', select('height', sizes, 1024))),
    field('Tiling', tile), cross,
    field('Hand-painted LoRA', h('input', { type: 'range', name: 'lora', min: 0, max: 1, step: 0.1, value: 0 }), 'Off by default. 0.8 helps grass, fabric and glazed tiles; it hurts plain walls and makes parquet ignore the plank layout.'),
    refBox, strengthRow, seedField(),
    h('details', {}, h('summary', {}, 'Advanced'), field('Steps', num('steps', '', { placeholder: '14 native / 8 cross-fade' })), check('raw', 'Raw prompt: no style hints', false)),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary block', type: 'button', onclick: () => { const p = readForm(form); if (!p.prompt.trim()) return toast('describe the material', 'err'); if (fs.reference) { p.reference_upload = fs.reference.path; p.reference_url = fs.reference.url; } else { delete p.strength; } submit('texture', p); } }, '▶ Generate texture')),
    h('div', { class: 'hint' }, '~20 s for 1024², ~50 s for 1024×2048.'));
  // show the LoRA value
  for (const r of form.querySelectorAll('input[type=range]')) { const out = h('span', { class: 'dim small', style: { marginLeft: '8px' } }, r.value); r.parentElement.querySelector('span').append(out); r.oninput = () => out.textContent = r.value; }
  return form;
}
function buildSpriteForm() {
  const fs = state.forms.sprite = state.forms.sprite || {};
  const form = h('form', { class: 'card form', onsubmit: e => e.preventDefault() });
  form.append(h('div', { class: 'card-head' }, h('h2', {}, '2D sprite: edit, pose or draw')),
    field('Prompt', h('textarea', { name: 'prompt', placeholder: 'The character from image 1 sitting on the office chair from image 2, seen from the front, hands on her knees, same character, same outfit and colours' }), 'Write what the finished sprite shows and what must stay (“same character, same outfit and colours”). Refer to inputs as image 1, image 2.'),
    field('Name', text('name', '', { placeholder: 'HeroSitFront (from prompt)' })),
    uploadBox('sprite', 'image', 'Source sprite = image 1', { hint: 'Leave empty to draw from text only', pick: pickSprite }),
    uploadBox('sprite', 'ref0', 'Reference = image 2 (optional)', { hint: 'e.g. a chair or prop the character interacts with', pick: pickSprite }),
    uploadBox('sprite', 'ref1', 'Reference = image 3 (optional)', { hint: '', pick: pickSprite }),
    field('Model', segf('model', [['distilled', 'Distilled (4 steps, ~30 s)', 'Fast iteration; colours may drift'], ['base', 'Base (20 steps, ~2 min)', 'Keeps exact colours, follows long prompts; use for final frames']], 'distilled')),
    field('Transparency', segf('alpha', [['auto', 'Cut out (BiRefNet)'], ['keep', 'Keep source alpha'], ['none', 'Opaque']], 'auto'), '“Keep source alpha” is exact for shading or colour passes that do not change the silhouette.'),
    h('div', { class: 'two' }, field('Canvas width', num('width', '', { placeholder: 'source / 768', step: 16 })), field('Canvas height', num('height', '', { placeholder: 'source / 1024', step: 16 }))),
    h('div', { class: 'hint', style: { marginTop: '-6px', marginBottom: '10px' } }, 'Both or neither, multiples of 16. Give a bigger canvas when a new pose needs room (sitting, arms out).'),
    seedField(),
    h('details', {}, h('summary', {}, 'Advanced'), h('div', { class: 'two' }, field('Steps', num('steps', '', { placeholder: '4 / 20' })), field('CFG (base only)', num('cfg', '', { placeholder: '5', step: 0.5 }))),
      field('Negative prompt (base only)', text('negative', 'blurry, photo, realistic, 3d render, extra limbs, text, watermark, drop shadow on the ground, cropped')),
      h('div', { class: 'two' }, field('Working megapixels', num('megapixels', 1.0, { step: 0.25, min: 0.25, max: 2 })), field('Flatten colour', text('bg', '#ffffff'))),
      check('match_source', 'Resize result back to the source pixel size', false), check('raw', 'Raw prompt: no style hints', false)),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary block', type: 'button', onclick: () => {
      const p = readForm(form); if (!p.prompt.trim()) return toast('write a prompt', 'err');
      if (fs.image) { p.image_upload = fs.image.path; p.image_url = fs.image.url; }
      p.ref_uploads = []; p.ref_urls = []; for (const k of ['ref0', 'ref1']) if (fs[k]) { p.ref_uploads.push(fs[k].path); p.ref_urls.push(fs[k].url); }
      if (p.ref_uploads.length && !fs.image) return toast('references need a source sprite (image 1)', 'err');
      if ((p.width || p.height) && !(p.width && p.height)) return toast('give both canvas width and height', 'err');
      submit('sprite', p);
    } }, '▶ Generate sprite')));
  return form;
}
function buildVoiceFormShell() { const form = h('form', { class: 'card form', id: 'voice-form', onsubmit: e => e.preventDefault() }, h('div', { class: 'empty' }, 'loading voices…')); return form; }
function buildVoiceForm() {
  const form = $('#voice-form'); if (!form) return;
  const prev = form.dataset.built ? readForm(form) : {};
  form.innerHTML = ''; form.dataset.built = '1';
  const fs = state.forms.voice = state.forms.voice || {};
  fs.voice = fs.voice || prev.voice || (state.voices[0] && state.voices[0].name);
  fs.mood = fs.mood || '';
  const vlist = h('div', { class: 'vlist' });
  const moodsBox = h('div', { class: 'moods' });
  const ftRow = h('div', {});
  const renderVoices = () => {
    vlist.innerHTML = '';
    for (const v of state.voices) vlist.append(h('div', { class: 'vcard' + (fs.voice === v.name ? ' on' : ''), onclick: () => { fs.voice = v.name; fs.mood = ''; renderVoices(); renderMoods(); } }, playBtn(v.ref_url), h('div', { class: 'grow' }, h('b', {}, v.name), h('span', {}, `${v.clips} clips · ${v.clip_seconds} s · ${v.moods.length ? v.moods.length + ' mood' + (v.moods.length > 1 ? 's' : '') : 'neutral only'}`)), h('span', { class: 'dim small', title: v.ref_text }, (v.ref_duration || 0) + ' s ref'), h('button', { class: 'btn xs', title: 'Clips, transcripts, moods', onclick: e => { e.stopPropagation(); openVoiceManager(v.name); } }, '⚙')));
  };
  const renderMoods = () => {
    const v = state.voices.find(x => x.name === fs.voice); moodsBox.innerHTML = ''; ftRow.innerHTML = '';
    if (!v) return;
    const pills = [{ name: '', label: 'Neutral', text: v.ref_text, url: v.ref_url }, ...v.moods.map(m => ({ name: m.name, label: m.name, text: m.text, url: m.url }))];
    for (const m of pills) moodsBox.append(h('span', { class: 'pill' + (fs.mood === m.name ? ' on' : ''), title: m.text, onclick: () => { fs.mood = m.name; renderMoods(); } }, h('span', { style: { textTransform: 'capitalize' } }, m.label), h('span', { class: 'play', style: { width: '20px', height: '20px', fontSize: '9px' }, onclick: e => { e.stopPropagation(); new Audio(m.url).play(); } }, '▶')));
    moodsBox.append(h('div', { class: 'hint', style: { width: '100%' } }, fs.mood ? `Delivery from ref_${fs.mood}.wav, timbre from ref.wav. ` : 'Add a mood with prep_voice.bat … --mood <name> --ref "clip" ', h('span', { class: 'dim' }, '“' + (pills.find(p => p.name === fs.mood)?.text || '').slice(0, 140) + '”')));
    if (v.finetuned.length) ftRow.append(field('Model', select('model', [['', 'Zero-shot clone (default)'], ...v.finetuned.map(f => [f.path, 'Fine-tuned: ' + f.name])], prev.model || ''), 'A fine-tuned checkpoint uses the voice as a trained speaker instead of cloning from ref.wav.'));
  };
  renderVoices(); renderMoods();
  const ta = h('textarea', { name: 'text', placeholder: 'I missed the last train home... Oh! Hey, Sam.', style: { minHeight: '110px' }, oninput: () => counter.textContent = ta.value.length + ' chars' });
  ta.value = prev.text || '';
  const counter = h('div', { class: 'counter' }, (ta.value.length) + ' chars');
  const tips = state.tips;
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Text → voice line')),
    h('div', { class: 'row', style: { marginBottom: '4px' } }, h('div', { class: 'label', style: { marginBottom: '0' } }, 'Voice'), h('button', { class: 'btn xs right', type: 'button', onclick: openAddVoice }, '＋ Add voice')), vlist,
    h('div', { class: 'label', style: { marginTop: '12px' } }, 'Mood'), moodsBox,
    h('div', { style: { marginTop: '12px' } }, field('Line', ta), counter),
    h('div', { class: 'two' }, field('Takes', select('takes', [1, 2, 3, 4, 5], prev.takes || 1), 'Several seeds in one go; keep the best.'), field('Language', select('language', ['English', 'German', 'French', 'Spanish', 'Italian', 'Portuguese', 'Russian', 'Japanese', 'Korean', 'Chinese', 'Auto'], prev.language || 'English'))),
    seedField(), ftRow,
    h('details', {}, h('summary', {}, 'Sampling'), h('div', { class: 'two' }, field('Temperature', num('temperature', prev.temperature ?? '', { placeholder: '0.9', step: 0.05, min: 0.1, max: 1.5 }), 'Lower = steadier, flatter read'), field('Top-k', num('top_k', prev.top_k ?? '', { placeholder: '50' }))),
      h('div', { class: 'two' }, field('Top-p', num('top_p', prev.top_p ?? '', { placeholder: '1.0', step: 0.05 })), field('Repetition penalty', num('repetition_penalty', prev.repetition_penalty ?? '', { placeholder: '1.05', step: 0.01 }))),
      check('mood_timbre', 'Take the timbre from the mood clip too (old behaviour)', !!prev.mood_timbre)),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary block', type: 'button', onclick: () => { const p = readForm(form); p.voice = fs.voice; p.mood = fs.mood || null; if (!p.text.trim()) return toast('write the line', 'err'); submit('voice', p); } }, '▶ Generate')),
    h('div', { class: 'hint' }, '~3 s of compute per second of audio; the first line also loads the model (~20 s). ComfyUI is asked to free its VRAM first.'));
  if (tips) {
    const t = h('div', { class: 'tips', style: { marginTop: '14px' } }, h('h3', {}, 'Acting through text'), h('div', { class: 'hint' }, tips.note));
    const tbl = h('table', {});
    for (const r of tips.documented) tbl.append(h('tr', {}, h('td', {}, h('code', {}, r.mark)), h('td', { class: 'muted' }, r.effect)));
    t.append(tbl, h('div', { class: 'dim small', style: { marginTop: '8px' } }, 'Not yet tested on this model (see todo/ai-voc-improvements.md):'));
    const tbl2 = h('table', {});
    for (const r of tips.untested) tbl2.append(h('tr', {}, h('td', {}, h('code', {}, r.mark)), h('td', { class: 'dim' }, r.effect)));
    t.append(tbl2);
    form.append(t);
  }
}

// ------------------------------------------------------------------ voice wizard + manager
function voicePanelHost() { return $('#page-voice .maincol'); }
function closeVoicePanels() { for (const el of document.querySelectorAll('.voice-panel')) el.remove(); }
function showVoicePanel(card) { closeVoicePanels(); card.classList.add('voice-panel'); voicePanelHost().prepend(card); card.scrollIntoView({ behavior: 'smooth', block: 'start' }); }

function openAddVoice() {
  const st = { folder: '', uploaded: 0 };
  const name = text('name', '', { placeholder: 'e.g. sam (lowercase)' });
  const folder = text('folder', '', { placeholder: 'C:\\recordings\\sam' });
  const fileInp = h('input', { type: 'file', accept: 'audio/*,.mp3,.wav,.flac,.ogg,.m4a', multiple: true, class: 'hidden' });
  const dropTxt = h('div', { class: 'txt' }, h('b', {}, 'or drop the audio files here'), h('br'), 'mp3 / wav / flac / ogg / m4a; they are copied into studio/uploads/voice_<name>/');
  const drop = h('div', { class: 'drop' }, dropTxt, fileInp);
  const take = async files => {
    const n = name.value.trim().toLowerCase();
    if (!n) return toast('give the voice a name first', 'err');
    let done = 0, folderPath = '';
    for (const f of files) { try { const r = await fetch(`/api/upload?name=${encodeURIComponent(f.name)}&folder=voice_${encodeURIComponent(n)}`, { method: 'POST', body: f }); const j = await r.json(); folderPath = j.folder; done++; dropTxt.innerHTML = ''; dropTxt.append(h('b', {}, `${done}/${files.length} uploaded`)); } catch (e) { toast('upload failed: ' + e.message, 'err'); } }
    if (folderPath) { folder.value = folderPath; st.uploaded = done; dropTxt.innerHTML = ''; dropTxt.append(h('b', {}, `${done} file${done > 1 ? 's' : ''} uploaded`), h('br'), folderPath); }
  };
  drop.onclick = () => fileInp.click();
  fileInp.onchange = () => take([...fileInp.files]);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); take([...e.dataTransfer.files]); };
  const form = h('form', { class: 'form', onsubmit: e => e.preventDefault() },
    field('Voice name', name, 'Becomes voices/<name>/. Lowercase letters, digits, _.'),
    field('Folder with the actor\'s lines', folder, 'A folder on this machine with one audio file per line. Keep the originals; everything in voices/ is derived.'),
    h('div', { class: 'field' }, drop),
    h('div', { class: 'three' }, field('Clip language', select('language', [['en', 'English'], ['nl', 'Dutch'], ['de', 'German'], ['fr', 'French'], ['es', 'Spanish'], ['it', 'Italian'], ['pt', 'Portuguese'], ['ja', 'Japanese'], ['ko', 'Korean'], ['zh', 'Chinese'], ['ru', 'Russian']], 'en')), field('Reference length (s)', num('ref_seconds', 12, { step: 1, min: 3, max: 30 })), field('Max clips in ref', num('max_ref_clips', 3, { min: 1, max: 6 }))),
    field('Reference clips (optional)', text('refs', '', { placeholder: 'substrings of file names, comma separated' }), 'Leave empty to let it pick the longest clean clips (2.5–12 s, a full sentence). Choose the actor\'s typical register: no screams, whispers, music or other voices.'),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary', type: 'button', onclick: () => {
      const p = readForm(form); p.name = p.name.trim().toLowerCase(); p.refs = (p.refs || '').split(',').map(x => x.trim()).filter(Boolean);
      if (!p.name) return toast('give the voice a name', 'err');
      if (!p.folder.trim()) return toast('give a folder or drop the audio files', 'err');
      submit('voice_prep', p); closeVoicePanels();
    } }, '▶ Prepare voice'), h('button', { class: 'btn', type: 'button', onclick: closeVoicePanels }, 'Cancel')),
    h('div', { class: 'hint' }, 'Whisper transcribes every clip on the GPU (the voice model is unloaded first), the clips are converted to 24 kHz mono, and ref.wav is cut from the clearest ones. Afterwards open ⚙ on the voice to fix any transcript and to add moods.'));
  showVoicePanel(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Add a voice'), h('button', { class: 'btn xs right', onclick: closeVoicePanels }, '✕')), form));
}

async function openVoiceManager(name) {
  let data;
  try { data = await api.get(`/api/voices/${name}/clips`); } catch (e) { return toast(e.message, 'err'); }
  const v = state.voices.find(x => x.name === name) || {};
  const edits = {}, refEdits = {};
  const refBox = h('div', { class: 'stack', style: { marginBottom: '12px' } });
  for (const [stem, txt] of Object.entries(data.refs)) {
    const url = stem === 'ref' ? v.ref_url : (v.moods || []).find(m => m.name === stem.slice(4))?.url;
    const ta = h('textarea', { style: { minHeight: '54px' }, oninput: () => refEdits[stem] = ta.value }); ta.value = txt;
    refBox.append(h('div', {}, h('div', { class: 'row', style: { marginBottom: '4px' } }, url ? playBtn(url) : null, h('b', {}, stem === 'ref' ? 'Neutral reference (ref.wav)' : `Mood “${stem.slice(4)}” (${stem}.wav)`)), ta));
  }
  const sel = new Set();
  const tbl = h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, h('input', { type: 'checkbox', title: 'select all', onchange: e => { for (const cb of tbl.querySelectorAll('tbody input[type=checkbox]')) { cb.checked = e.target.checked; cb.dispatchEvent(new Event('change')); } } })), h('th', {}, '#'), h('th', {}, ''), h('th', {}, 'Length'), h('th', {}, 'Transcript (edit if Whisper got it wrong)'), h('th', {}, 'Source file'))));
  const body = h('tbody');
  for (const c of data.clips) {
    const inp = h('input', { type: 'text', value: c.text, style: { width: '100%', background: 'transparent', border: '1px solid transparent', padding: '3px 6px', borderRadius: '6px' }, onfocus: e => e.target.style.borderColor = 'var(--line2)', onblur: e => e.target.style.borderColor = 'transparent', oninput: e => edits[c.index] = e.target.value });
    const cb = h('input', { type: 'checkbox', onchange: e => { e.target.checked ? sel.add(c.number) : sel.delete(c.number); count.textContent = sel.size ? `${sel.size} selected` : ''; } });
    body.append(h('tr', {}, h('td', {}, cb), h('td', { class: 'mono' }, c.number), h('td', {}, c.url ? playBtn(c.url) : h('span', { class: 'dim small' }, 'no wav')), h('td', { class: 'small' }, c.duration ? c.duration + ' s' : ''), h('td', {}, inp), h('td', { class: 'dim small', title: c.source }, (c.source || '').slice(0, 28))));
  }
  tbl.append(body);
  const count = h('span', { class: 'dim small' });
  const moodName = text('mood', '', { placeholder: 'mood name, e.g. upset' }); moodName.style.width = '180px';
  const save = async () => {
    try { const r = await api.post(`/api/voices/${name}/clips`, { texts: edits, refs: refEdits }); toast(`saved (${r.changed} transcript${r.changed === 1 ? '' : 's'} changed)`, 'ok'); for (const k in edits) delete edits[k]; for (const k in refEdits) delete refEdits[k]; loadLibrary('voice', true); } catch (e) { toast(e.message, 'err'); }
  };
  const card = h('div', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', {}, `Voice: ${name}`), h('span', { class: 'dim small' }, `${data.clips.length} clips`), h('button', { class: 'btn xs right', onclick: closeVoicePanels }, '✕')),
    h('h3', {}, 'References'), h('div', { class: 'hint', style: { marginBottom: '8px' } }, 'The model continues the reference, so its transcript must match the audio exactly.'), refBox,
    h('h3', {}, 'Clips'), h('div', { class: 'hint', style: { marginBottom: '8px' } }, 'Tick the clips spoken in one emotion and give the mood a name: 2–3 full sentences, 4–12 s in total, never a lone yell. Uncheck nothing to keep the fine-tune set as is.'),
    h('div', { style: { maxHeight: '420px', overflow: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--radius-s)' } }, tbl),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary', onclick: save }, 'Save transcripts'), h('span', { style: { width: '12px' } }), moodName,
      h('button', { class: 'btn', onclick: async () => { const m = moodName.value.trim().toLowerCase(); if (!m) return toast('name the mood', 'err'); if (!sel.size) return toast('tick the clips spoken in that mood', 'err'); if (Object.keys(edits).length || Object.keys(refEdits).length) await save(); submit('voice_prep', { name, mood: m, refs: [...sel].sort() }); closeVoicePanels(); } }, '＋ Add mood from selected'), count));
  showVoicePanel(card);
}

function resultVoicePrep(job) {
  const o = job.outputs?.voice || {};
  return h('div', {}, h('div', { class: 'muted' }, o.mood ? `Mood “${o.mood}” added to ${o.name}. It is now in the mood list; its transcript can be checked under ⚙.` : `Voice “${o.name}” is ready. Check the transcripts under ⚙ on the voice card: Whisper mistakes in ref.txt hurt every line.`),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary sm', onclick: () => openVoiceManager(o.name) }, '⚙ Review clips and transcripts')));
}

// ------------------------------------------------------------------ music (ACE-Step 1.5)
function resultMusic(job, lib) {
  const files = job?.outputs?.files || (lib ? [lib] : []);
  if (!files.length) return h('div', { class: 'empty' }, 'No audio yet');
  const p = job?.params || lib?.job?.params || {};
  const box = h('div', {}, h('div', { class: 'muted', style: { marginBottom: '8px' } }, '“', p.caption || lib?.caption || '', '”'));
  const tbl = h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Take'), h('th', {}, 'Audio'), h('th', {}, 'Seed'), h('th', {}, 'Length'), h('th', {}, 'Planned'), h('th', {}, ''))));
  files.forEach((f, i) => { const m = f.meta || {}; tbl.append(h('tr', {}, h('td', {}, files.length > 1 ? `#${i + 1}` : basename(f.url)), h('td', {}, h('audio', { controls: true, preload: 'none', src: f.url, loop: !!p.loop })), h('td', { class: 'mono' }, f.seed ?? ''), h('td', {}, f.duration ? fmtDur(f.duration) : ''),
    h('td', { class: 'small dim' }, [m.lm_bpm ? m.lm_bpm + ' bpm' : null, m.lm_keyscale].filter(Boolean).join(' · ')),
    h('td', { style: { whiteSpace: 'nowrap' } }, dl(f.url, basename(f.url).split('.').pop().toUpperCase()), ' ', reveal(f.path), ' ', h('button', { class: 'btn sm', title: 'Use this track as the style reference for the next one', onclick: () => setMusicRef({ path: f.path, url: f.url, name: basename(f.path) }) }, '♪ Reference')))); });
  const m0 = files[0]?.meta || {};
  box.append(tbl, kv([['Length', p.duration ? p.duration + ' s asked' : null], ['Loop', p.loop ? `yes, ${p.crossfade || 2} s crossfade` : null], ['Lyrics', (p.lyrics || '').trim() ? h('span', { style: { whiteSpace: 'pre-wrap' } }, p.lyrics.trim().slice(0, 400)) : null],
    ['Tempo / key', [p.bpm ? p.bpm + ' bpm' : null, p.keyscale, p.timesignature ? p.timesignature + '/4' : null].filter(Boolean).join(' · ')], ['Planner LM', p.think === false ? 'off' : (p.lm || 'acestep-5Hz-lm-0.6B')],
    ['LM caption', m0.lm_caption && m0.lm_caption !== p.caption ? m0.lm_caption : null], ['Reference', p.reference_upload ? basename(p.reference_upload) : null]]));
  return box;
}
function musicTable(items) {
  const tbl = h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, 'Date'), h('th', {}, 'Track'), h('th', {}, 'Caption'), h('th', {}, 'Length'), h('th', {}, 'Seed'), h('th', {}, ''))));
  for (const it of items) {
    const job = it.job ? Object.assign({ kind: 'music', status: 'done', id: it.job.id }, it.job) : null;
    const m = it.meta || {};
    tbl.append(h('tr', {}, h('td', {}, playBtn(it.url)), h('td', { class: 'small', style: { whiteSpace: 'nowrap' } }, fmtDate(it.mtime)),
      h('td', {}, h('b', {}, it.name), h('div', { class: 'dim small' }, [it.loop ? 'loop' : null, it.vocal ? 'vocal' : null, m.lm_bpm ? m.lm_bpm + ' bpm' : null, m.lm_keyscale].filter(Boolean).join(' · '))),
      h('td', {}, h('span', { class: 'text', title: it.caption || it.file }, it.caption || it.file)), h('td', { class: 'small' }, it.duration ? fmtDur(it.duration) : ''), h('td', { class: 'mono' }, it.seed ?? ''),
      h('td', { style: { whiteSpace: 'nowrap' } }, job ? h('button', { class: 'btn xs', title: 'Load this caption and its settings into the form', onclick: () => reuse(job) }, '↻') : null, ' ', h('button', { class: 'btn xs', title: 'Use as style reference', onclick: () => setMusicRef({ path: it.path, url: it.url, name: it.file }) }, '♪'), ' ', dl(it.url, it.file.split('.').pop().toUpperCase()))));
  }
  return tbl;
}
function setMusicRef(v) { const fs = state.forms.music = state.forms.music || {}; fs.reference = v; const f = $('#music-form'); if (f) for (const u of f.querySelectorAll('.field')) if (u.refresh) u.refresh(); f?.scrollIntoView({ behavior: 'smooth' }); toast('reference set'); }
function audioUploadBox(kind, key, label, hint) {
  const fs = state.forms[kind] = state.forms[kind] || {};
  const inp = h('input', { type: 'file', accept: 'audio/*,.wav,.mp3,.flac,.ogg,.m4a', class: 'hidden' });
  const box = h('div', { class: 'drop' });
  const render = () => {
    const v = fs[key]; box.innerHTML = '';
    if (v) box.append(h('audio', { controls: true, preload: 'none', src: v.url, onclick: e => e.stopPropagation() }), h('div', { class: 'txt' }, h('b', {}, v.name || basename(v.path)), h('br'), h('span', { class: 'dim' }, label)), h('span', { class: 'x', title: 'remove', onclick: e => { e.stopPropagation(); delete fs[key]; render(); } }, '✕'));
    else box.append(h('div', { class: 'txt' }, h('b', {}, label), h('br'), hint));
  };
  box.onclick = () => inp.click();
  box.ondragover = e => { e.preventDefault(); box.classList.add('over'); };
  box.ondragleave = () => box.classList.remove('over');
  const take = async file => { if (!file) return; try { const u = await api.upload(file); fs[key] = u; render(); } catch (e) { toast('upload failed: ' + e.message, 'err'); } };
  box.ondrop = e => { e.preventDefault(); box.classList.remove('over'); take(e.dataTransfer.files[0]); };
  inp.onchange = () => take(inp.files[0]);
  render();
  const wrap = h('div', { class: 'field' }, box, inp);
  wrap.refresh = render;
  return wrap;
}
function buildMusicFormShell() { const form = h('form', { class: 'card form', id: 'music-form', onsubmit: e => e.preventDefault() }, h('div', { class: 'empty' }, 'loading…')); return form; }
function buildMusicForm() {
  const form = $('#music-form'); if (!form) return;
  const prev = form.dataset.built ? readForm(form) : {};
  form.innerHTML = ''; form.dataset.built = '1';
  const fs = state.forms.music = state.forms.music || {};
  const info = state.music || { models: { lms: [], dits: [], ready: false }, tips: null };
  const ta = h('textarea', { name: 'caption', placeholder: 'calm piano and soft strings, a quiet village at dusk, gentle and warm, slow, no vocals', style: { minHeight: '90px' }, oninput: () => counter.textContent = ta.value.length + ' / 512 chars', maxlength: 512 });
  ta.value = prev.caption || '';
  const counter = h('div', { class: 'counter' }, ta.value.length + ' / 512 chars');
  const lyr = h('textarea', { name: 'lyrics', placeholder: '[Verse]\nMorning light on cobblestone\n...\n\n[Chorus]\n...', style: { minHeight: '110px' } });
  lyr.value = prev.lyrics || '';
  const lmOpts = [...info.models.lms.map(n => [n, n.replace('acestep-5Hz-lm-', 'Planner LM ') + (n.endsWith('0.6B') ? ' (fits 8 GB)' : ' (12 GB+, needs offload)')]), ['', 'No planner: DiT only']];
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Text → music')),
    ...(info.models.ready ? [] : [h('div', { class: 'errbox' }, 'ai-mus is not set up: run ai-mus/setup.ps1 (clones ACE-Step 1.5 and downloads ~11 GB of weights).')]),
    field('Caption', ta), counter,
    h('div', { class: 'two' }, field('Length (s)', num('duration', prev.duration ?? 60, { min: 10, max: 600, step: 5 }), '10–600; loops: 30–90'), field('Takes', select('takes', [1, 2, 3, 4, 5, 6], prev.takes || 1), 'Consecutive seeds; keep the best')),
    check('loop', 'Seamless loop (crossfade the end into the start)', prev.loop ?? true, 'The last seconds are blended into the first ones and cut off'),
    h('div', { class: 'two' }, field('Crossfade (s)', num('crossfade', prev.crossfade ?? 2, { min: 0.5, max: 8, step: 0.5 })), field('Format', select('format', [['wav', 'WAV 16-bit'], ['ogg', 'OGG Vorbis'], ['flac', 'FLAC']], prev.format || 'wav'))),
    field('Name', text('name', prev.name || '', { placeholder: 'VillageDusk (from caption)' })),
    seedField(),
    h('details', {}, h('summary', {}, 'Vocals (lyrics)'), field('Lyrics', lyr, 'Leave empty for an instrumental. [Verse] / [Chorus] / [Bridge] tags shape the song.'), field('Language', select('language', [['en', 'English'], ['de', 'German'], ['fr', 'French'], ['nl', 'Dutch'], ['es', 'Spanish'], ['it', 'Italian'], ['pt', 'Portuguese'], ['ja', 'Japanese'], ['ko', 'Korean'], ['zh', 'Chinese']], prev.language || 'en'))),
    h('details', {}, h('summary', {}, 'Tempo, key, time signature'), h('div', { class: 'three' }, field('BPM', num('bpm', prev.bpm ?? '', { placeholder: 'auto', min: 30, max: 300 })), field('Key', text('keyscale', prev.keyscale || '', { placeholder: 'A minor' })), field('Time sig.', select('timesignature', [['', 'auto'], ['4', '4/4'], ['3', '3/4'], ['2', '2/4'], ['6', '6/8']], prev.timesignature || '')))),
    h('details', {}, h('summary', {}, 'Reference audio (style transfer)'), audioUploadBox('music', 'reference', 'Reference track (optional)', 'Drop a wav/mp3 here, or pick ♪ on a library track; the new piece borrows its style and timbre')),
    h('details', {}, h('summary', {}, 'Model'), field('Planner', select('lm', lmOpts, prev.lm ?? (info.models.lms.includes('acestep-5Hz-lm-0.6B') ? 'acestep-5Hz-lm-0.6B' : (info.models.lms[0] || ''))), 'The LM plans bpm, key, structure and semantic codes before the DiT. Off = faster, less structured.'),
      h('div', { class: 'two' }, field('Steps', num('steps', prev.steps ?? 8, { min: 1, max: 20 }), 'Turbo DiT: 8'), field('LM temperature', num('lm_temperature', prev.lm_temperature ?? 0.85, { min: 0.1, max: 2, step: 0.05 })))),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary block', type: 'button', onclick: () => { const p = readForm(form); if (!p.caption.trim()) return toast('describe the music', 'err'); p.think = p.lm !== ''; if (fs.reference) { p.reference_upload = fs.reference.path; p.reference_url = fs.reference.url; } submit('music', p); } }, '▶ Compose')),
    h('div', { class: 'hint' }, 'About 1 s of compute per second of music on the 2080 after the ~40 s model load. The voice worker is unloaded and ComfyUI is asked to free its VRAM first.'));
  const tips = info.tips;
  if (tips) {
    const t = h('div', { class: 'tips', style: { marginTop: '14px' } }, h('h3', {}, 'Writing the caption'), h('div', { class: 'hint' }, tips.note));
    const tbl = h('table', {});
    for (const r of tips.documented) tbl.append(h('tr', {}, h('td', {}, h('code', {}, r.mark)), h('td', { class: 'muted' }, r.effect)));
    t.append(tbl, h('div', { class: 'dim small', style: { marginTop: '8px' } }, 'Not yet tested here:'));
    const tbl2 = h('table', {});
    for (const r of tips.untested) tbl2.append(h('tr', {}, h('td', {}, h('code', {}, r.mark)), h('td', { class: 'dim' }, r.effect)));
    t.append(tbl2);
    form.append(t);
  }
}

// ------------------------------------------------------------------ sound effects (MOSS-SoundEffect v2.0)
function resultSfx(job, lib) {
  const files = job?.outputs?.files || (lib ? [lib] : []);
  if (!files.length) return h('div', { class: 'empty' }, 'No audio yet');
  const p = job?.params || lib?.job?.params || {};
  const box = h('div', {}, h('div', { class: 'muted', style: { marginBottom: '8px' } }, '“', p.prompt || lib?.prompt || '', '”'));
  const tbl = h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Take'), h('th', {}, 'Audio'), h('th', {}, 'Seed'), h('th', {}, 'Length'), h('th', {}, ''))));
  files.forEach((f, i) => tbl.append(h('tr', {}, h('td', {}, files.length > 1 ? `#${i + 1}` : basename(f.url)), h('td', {}, h('audio', { controls: true, preload: 'none', src: f.url, loop: !!p.loop })), h('td', { class: 'mono' }, f.seed ?? ''), h('td', {}, f.duration != null ? f.duration + ' s' : ''),
    h('td', { style: { whiteSpace: 'nowrap' } }, dl(f.url, basename(f.url).split('.').pop().toUpperCase()), ' ', reveal(f.path)))));
  box.append(tbl, kv([['Length', p.duration ? p.duration + ' s asked (silence trimmed)' : null], ['Loop', p.loop ? `yes, ${p.crossfade || 1} s crossfade` : null], ['Negative', p.negative || null],
    ['Level', p.normalize === false ? 'raw' : `peak ${p.peak_db ?? -1} dBFS`], ['Steps / guidance', `${p.steps || 50} / ${p.cfg || 4}`]]));
  return box;
}
function sfxTable(items) {
  const tbl = h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, 'Date'), h('th', {}, 'Sound'), h('th', {}, 'Prompt'), h('th', {}, 'Length'), h('th', {}, 'Seed'), h('th', {}, ''))));
  for (const it of items) {
    const job = it.job ? Object.assign({ kind: 'sfx', status: 'done', id: it.job.id }, it.job) : null;
    tbl.append(h('tr', {}, h('td', {}, playBtn(it.url)), h('td', { class: 'small', style: { whiteSpace: 'nowrap' } }, fmtDate(it.mtime)),
      h('td', {}, h('b', {}, it.name), it.loop ? h('div', { class: 'dim small' }, 'loop') : null),
      h('td', {}, h('span', { class: 'text', title: it.prompt || it.file }, it.prompt || it.file)), h('td', { class: 'small' }, it.duration != null ? it.duration + ' s' : ''), h('td', { class: 'mono' }, it.seed ?? ''),
      h('td', { style: { whiteSpace: 'nowrap' } }, job ? h('button', { class: 'btn xs', title: 'Load this prompt and its settings into the form', onclick: () => reuse(job) }, '↻') : null, ' ', dl(it.url, it.file.split('.').pop().toUpperCase()))));
  }
  return tbl;
}
// one-click starting points: label, prompt, length, loop
const SFX_EXAMPLES = [
  ['Explosion', 'a huge explosion with a deep boom and debris falling, outdoors', 5, false],
  ['Gunshot', 'a single pistol gunshot, sharp and loud, short echo in a concrete room', 1.5, false],
  ['Footsteps', 'footsteps of boots walking on gravel, steady pace', 4, false],
  ['Sword', 'a metal sword swung fast through the air and clashing against another sword', 1.5, false],
  ['UI click', 'a soft short user-interface click, clean and bright', 0.5, false],
  ['Pickup', 'a bright magical chime for picking up a coin in a video game', 1, false],
  ['Rain loop', 'steady rain falling on a tin roof, no thunder', 15, true],
];
function buildSfxFormShell() { const form = h('form', { class: 'card form', id: 'sfx-form', onsubmit: e => e.preventDefault() }, h('div', { class: 'empty' }, 'loading…')); return form; }
function buildSfxForm() {
  const form = $('#sfx-form'); if (!form) return;
  const prev = form.dataset.built ? readForm(form) : {};
  form.innerHTML = ''; form.dataset.built = '1';
  const info = state.sfx || { ready: { ready: false }, tips: null };
  const ta = h('textarea', { name: 'prompt', placeholder: 'a single pistol gunshot in a small concrete room, sharp, short echo', style: { minHeight: '80px' } });
  ta.value = prev.prompt || '';
  const examples = h('div', { class: 'row', style: { flexWrap: 'wrap', gap: '6px', marginBottom: '10px' } }, SFX_EXAMPLES.map(([label, prompt, duration, loop]) => h('button', { type: 'button', class: 'btn xs', title: prompt, onclick: () => fillForm(form, { prompt, duration, loop, name: '' }) }, label)));
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Text → sound effect')),
    ...(info.ready.ready ? [] : [h('div', { class: 'errbox' }, 'ai-sfx is not set up: run ai-sfx/setup.ps1 (clones MOSS-TTS and downloads ~11 GB of weights).')]),
    field('Sound', ta, 'What makes the sound, what it does, and where: source + action + space.'), examples,
    h('div', { class: 'two' }, field('Length (s)', num('duration', prev.duration ?? 4, { min: 0.5, max: 30, step: 0.5 }), '0.5–30; silence at the end is trimmed'), field('Takes', select('takes', [1, 2, 3, 4, 5, 6, 8], prev.takes || 4), 'Consecutive seeds; keep the best')),
    check('loop', 'Seamless loop (for ambiences)', prev.loop ?? false, 'The last second is blended into the start and cut off'),
    h('div', { class: 'two' }, field('Name', text('name', prev.name || '', { placeholder: 'PistolShot (from prompt)' })), field('Format', select('format', [['wav', 'WAV 16-bit'], ['ogg', 'OGG Vorbis'], ['flac', 'FLAC']], prev.format || 'wav'))),
    seedField(),
    h('details', {}, h('summary', {}, 'Advanced'),
      field('Negative prompt', text('negative', prev.negative || '', { placeholder: 'music, speech, voices' })),
      h('div', { class: 'three' }, field('Steps', num('steps', prev.steps ?? 50, { min: 10, max: 200 }), 'upstream: 100'), field('Guidance', num('cfg', prev.cfg ?? 4, { min: 1, max: 10, step: 0.5 })), field('Peak (dBFS)', num('peak_db', prev.peak_db ?? -1, { min: -24, max: 0, step: 0.5 }))),
      field('Low-cut (Hz)', num('highpass', prev.highpass ?? 30, { min: 0, max: 500, step: 5 }), 'Removes sub-bass drift; 0 = off'),
      check('trim', 'Trim leading/trailing silence', prev.trim ?? true), check('normalize', 'Peak-normalize', prev.normalize ?? true),
      field('Loop crossfade (s)', num('crossfade', prev.crossfade ?? 1, { min: 0.1, max: 5, step: 0.1 }))),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary block', type: 'button', onclick: () => { const p = readForm(form); if (!p.prompt.trim()) return toast('describe the sound', 'err'); submit('sfx', p); } }, '▶ Generate')),
    h('div', { class: 'hint' }, 'About 25 s per take on the 2080 whatever the length, after a ~20 s model load. The other workers are unloaded and ComfyUI is asked to free its VRAM first.'));
  const tips = info.tips;
  if (tips) {
    const t = h('div', { class: 'tips', style: { marginTop: '14px' } }, h('h3', {}, 'Writing the prompt'), h('div', { class: 'hint' }, tips.note));
    const tbl = h('table', {});
    for (const r of tips.documented) tbl.append(h('tr', {}, h('td', {}, h('code', {}, r.mark)), h('td', { class: 'muted' }, r.effect)));
    t.append(tbl, h('div', { class: 'dim small', style: { marginTop: '8px' } }, 'Not yet tested here:'));
    const tbl2 = h('table', {});
    for (const r of tips.untested) tbl2.append(h('tr', {}, h('td', {}, h('code', {}, r.mark)), h('td', { class: 'dim' }, r.effect)));
    t.append(tbl2);
    form.append(t);
  }
}

// ------------------------------------------------------------------ overview
async function renderOverview() {
  const page = $('#page-overview'); const s = state.system; if (!s) return;
  const jobs = [...state.jobs.values()].sort((a, b) => b.created - a.created);
  const running = jobs.filter(j => j.status === 'running'), queued = jobs.filter(j => j.status === 'queued');
  const keep = page.querySelector('details[open]') ? true : false;
  page.innerHTML = '';
  const g = s.gpu;
  const wstate = w => w?.model_loaded ? 'model loaded' : w?.process === 'running' ? 'idle' : 'off';
  const wbtns = (w, base, label) => h('div', { class: 'row' }, w?.process === 'running' ? [h('button', { class: 'btn xs', title: 'Free the GPU, keep the process', onclick: () => api.post(`${base}/unload`).then(() => toast(`${label} worker unloaded`)).catch(e => toast(e.message, 'err')) }, 'Unload'), h('button', { class: 'btn xs', onclick: () => api.post(`${base}/stop`).then(() => toast(`${label} worker stopped`)).catch(e => toast(e.message, 'err')) }, 'Stop')] : null);
  page.append(h('div', { class: 'stats' },
    h('div', { class: 'stat' }, h('div', { class: 'k' }, 'ComfyUI'), h('div', { class: 'v' }, s.comfy.alive ? 'running' : s.comfy.process), h('div', { class: 's' }, s.comfy.alive ? `${s.comfy.queue.running} running · ${s.comfy.queue.pending} pending in its queue` : 'image, 3D, texture, sprite and video models'), h('div', { class: 'row' }, comfyButton(s.comfy), s.comfy.process === 'running' ? h('button', { class: 'btn xs', onclick: () => api.post('/api/comfy/stop').then(() => toast('stopping ComfyUI')).catch(e => toast(e.message, 'err')) }, 'Stop') : null)),
    h('div', { class: 'stat' }, h('div', { class: 'k' }, 'Voice worker'), h('div', { class: 'v' }, wstate(s.tts)), h('div', { class: 's' }, 'Qwen3-TTS stays loaded between lines and is unloaded before ComfyUI jobs'), wbtns(s.tts, '/api/tts', 'voice')),
    h('div', { class: 'stat' }, h('div', { class: 'k' }, 'Music worker'), h('div', { class: 'v' }, wstate(s.music)), h('div', { class: 's' }, 'ACE-Step 1.5 stays loaded between tracks and is unloaded before other jobs'), wbtns(s.music, '/api/music', 'music')),
    h('div', { class: 'stat' }, h('div', { class: 'k' }, 'SFX worker'), h('div', { class: 'v' }, wstate(s.sfx)), h('div', { class: 's' }, 'MOSS-SoundEffect v2.0 stays loaded between sounds and is unloaded before other jobs'), wbtns(s.sfx, '/api/sfx', 'sfx')),
    h('div', { class: 'stat' }, h('div', { class: 'k' }, 'Animate worker'), h('div', { class: 'v' }, s.anim?.model_loaded ? 'models loaded' : wstate(s.anim)), h('div', { class: 's' }, 'Make-It-Animatable + Kimodo (Llama-3 encoder in RAM) stay loaded between characters and are unloaded before other jobs'), wbtns(s.anim, '/api/anim', 'animation')),
    h('div', { class: 'stat' }, h('div', { class: 'k' }, 'GPU'), h('div', { class: 'v' }, g.available ? `${(g.used_mb / 1024).toFixed(1)} / ${(g.total_mb / 1024).toFixed(0)} GB` : 'n/a'), h('div', { class: 's' }, g.available ? `${g.name} · ${g.util}% busy` : 'nvidia-smi not found')),
    h('div', { class: 'stat' }, h('div', { class: 'k' }, 'Queue'), h('div', { class: 'v' }, `${running.length} running · ${queued.length} queued`), h('div', { class: 's' }, 'Jobs run one after another so only one model set holds the GPU. Scripts and Claude use the same queue (see studio/API.md).'))));
  const list = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Jobs'), h('span', { class: 'dim small' }, `${jobs.length} total`)));
  if (!jobs.length) list.append(h('div', { class: 'empty' }, 'No jobs yet. Pick a flow in the top bar.'));
  for (const j of jobs.slice(0, 60)) list.append(h('div', { class: 'qrow' }, h('span', { class: 'badge ' + j.status }, j.status), h('span', { style: { cursor: 'pointer' }, onclick: () => { state.focus[pageOf(j.kind)] = j.id; go(pageOf(j.kind)); } }, h('span', { class: 'dim small' }, KIND_LABEL[j.kind] + ' · '), j.title),
    h('span', {}, j.status === 'running' ? h('div', { class: 'mini' }, h('i', { style: { width: (j.progress?.pct || 0) + '%' } })) : h('span', { class: 'dim small' }, j.error ? j.error.slice(0, 60) : j.progress?.phase || '')), h('span', { class: 'dim small' }, fmtDate(j.created) + (j.started ? ' · ' + elapsed(j) : '')), h('span', {}, ...jobActions(j).slice(0, 1))));
  page.append(list);
  const comfyLog = h('div', { class: 'card' }, h('details', { open: keep }, h('summary', { style: { cursor: 'pointer' } }, h('b', {}, 'ComfyUI process log'), h('span', { class: 'dim small' }, ' (only when started from the studio)')), h('div', { class: 'pre', id: 'comfy-log' }, 'loading…')));
  comfyLog.querySelector('details').addEventListener('toggle', async e => { if (e.target.open) { const l = await api.get('/api/comfy/log'); $('#comfy-log').textContent = l.lines.join('\n') || `(no output captured; process state: ${l.state})`; } });
  if (keep) { const l = await api.get('/api/comfy/log'); $('#comfy-log').textContent = l.lines.join('\n') || '(no output captured)'; }
  page.append(comfyLog);
}

// ------------------------------------------------------------------ boot
(async () => {
  await loadState();
  connect();
  go(state.page in KIND_LABEL || state.page === 'overview' ? state.page : 'overview');
  window.addEventListener('hashchange', () => { const p = location.hash.slice(1); if (p !== state.page && (p in KIND_LABEL || p === 'overview')) go(p); });
  setInterval(() => { for (const k of Object.keys(activity)) { const j = state.focus[k] && state.jobs.get(state.focus[k]); if (j && j.status === 'running' && activity[k].el.update) activity[k].el.update(j); } }, 1000);
})();

// ------------------------------------------------------------------ video (ai-vid: Wan 2.2 shot list + logo reveal)
const VIDEO_SIZES = [['832x480', '480p · 832×480 (fits 8 GB, ~40 s per second of video)'], ['1024x576', '576p · 1024×576'], ['1280x704', '720p · 1280×704 (slow, may not fit)'], ['480x832', '480p portrait · 480×832'], ['640x640', 'Square · 640×640']];
function frameFromSpec(v) {
  // JSON shot-list start/end -> form struct {mode, image, prompt, ref, model, seed}
  if (!v) return { mode: 'none' };
  if (v === 'previous') return { mode: 'previous' };
  if (v.image) return { mode: 'image', image: { path: v.image, url: v.image_url || null, name: basename(v.image) } };
  if (v.generate) return { mode: 'generate', prompt: v.generate, ref: (v.refs || [])[0] ? { path: v.refs[0], url: v.ref_urls?.[0] || null, name: basename(v.refs[0]) } : null, model: v.model || 'base', seed: v.seed ?? null };
  return { mode: 'none' };
}
function frameToSpec(f) {
  if (!f || f.mode === 'none') return null;
  if (f.mode === 'previous') return 'previous';
  if (f.mode === 'image') return f.image ? { image: f.image.path, image_url: f.image.url } : null;
  if (f.mode === 'generate') return { generate: f.prompt || '', refs: f.ref ? [f.ref.path] : [], ref_urls: f.ref ? [f.ref.url] : [], model: f.model || 'base', seed: f.seed ?? null };
  return null;
}
function shotFromSpec(s, i) {
  const t = s.type === 'logo' ? 'logo' : 'video';
  return Object.assign({ id: s.id || `shot${i + 1}`, type: t, seconds: s.seconds ?? 3, transition: s.transition ?? null },
    t === 'logo' ? { logo: s.logo ? { path: s.logo, url: s.logo_url || null, name: basename(s.logo) } : null, split: s.split ?? 0.69, background: s.background || '#ffffff', from: s.from ?? 'previous', scale: s.scale ?? 0.8 }
      : { prompt: s.prompt || '', seed: s.seed ?? null, steps: s.steps ?? null, cfg: s.cfg ?? null, start: frameFromSpec(s.start), end: frameFromSpec(s.end) });
}
function shotToSpec(s) {
  const base = { id: s.id, seconds: Number(s.seconds) || 3 };
  if (s.transition != null && s.transition !== '') base.transition = Number(s.transition);
  if (s.type === 'logo') return Object.assign(base, { type: 'logo', logo: s.logo?.path || 'assets/logo.png', logo_url: s.logo?.url, split: Number(s.split) || 0.69, background: s.background || '#ffffff', from: s.from || 'previous', scale: Number(s.scale) || 0.8 });
  const o = Object.assign(base, { prompt: s.prompt || '', start: frameToSpec(s.start), end: frameToSpec(s.end) });
  if (s.seed != null && s.seed !== '') o.seed = Number(s.seed);
  if (s.steps) o.steps = Number(s.steps);
  if (s.cfg) o.cfg = Number(s.cfg);
  return o;
}
function videoFormFromSpec(spec) {
  const fs = state.forms.video = state.forms.video || {};
  fs.name = spec.name || 'intro'; fs.width = spec.width || 832; fs.height = spec.height || 480; fs.fps = spec.fps || 24;
  fs.transition = spec.transition ?? 0; fs.transition_kind = spec.transition_kind || 'fade'; fs.negative = spec.negative || '';
  fs.audio = spec.audio ? { path: spec.audio, url: spec.audio_url || null, name: basename(spec.audio) } : null;
  fs.shots = (spec.shots || []).map(shotFromSpec);
  return fs;
}
function videoParams(fs, mode, only) {
  return { name: fs.name, width: Number(fs.width), height: Number(fs.height), fps: Number(fs.fps) || 24, transition: Number(fs.transition) || 0,
    transition_kind: fs.transition_kind || 'fade', negative: fs.negative || null, audio_upload: fs.audio?.path || null, audio_url: fs.audio?.url || null,
    shots: fs.shots.map(shotToSpec), mode, only: only || [] };
}
function buildVideoFormShell() { return h('form', { class: 'card form', id: 'video-form', onsubmit: e => e.preventDefault() }, h('div', { class: 'empty' }, 'loading…')); }
function buildVideoForm() {
  const form = $('#video-form'); if (!form) return;
  form.innerHTML = ''; form.dataset.built = '1';
  const info = state.video || { presets: [], ffmpeg: true, models: true };
  const fs = state.forms.video = state.forms.video || {};
  if (!fs.shots) {
    const preset = info.presets.find(p => p.file === 'example_intro.json') || info.presets[0];
    videoFormFromSpec(preset ? preset.spec : { name: 'intro', shots: [{ id: 'shot1', prompt: '', seconds: 3, start: null }] });
  }
  const sizeVal = `${fs.width}x${fs.height}`;
  const presetSel = select('preset', info.presets.map(p => [p.file, p.spec.name || p.file]), info.presets[0]?.file);
  const nameInp = text('name', fs.name, { placeholder: 'my_intro', oninput: e => fs.name = e.target.value });
  const sizeSel = select('size', VIDEO_SIZES, VIDEO_SIZES.some(s => s[0] === sizeVal) ? sizeVal : '832x480'); sizeSel.onchange = () => { const [w, hgt] = sizeSel.value.split('x').map(Number); fs.width = w; fs.height = hgt; };
  const kindSel = select('transition_kind', ['fade', 'dissolve', 'fadeblack', 'fadewhite', 'wipeup', 'slideup', 'circleopen', 'pixelize'], fs.transition_kind); kindSel.onchange = () => fs.transition_kind = kindSel.value;
  const shotsBox = h('div', { class: 'shots' });
  const renderShots = () => { shotsBox.innerHTML = ''; fs.shots.forEach((s, i) => shotsBox.append(shotCard(s, i, fs, renderShots))); };
  renderShots();
  const jsonTa = h('textarea', { style: { minHeight: '160px', fontFamily: 'var(--mono)', fontSize: '12px' } });
  const jsonBox = h('details', {}, h('summary', {}, 'Edit as JSON (the shot-list format intro.bat reads)'),
    h('div', { class: 'hint' }, 'Open to see the current shot list as JSON; edit and press Apply.'), jsonTa,
    h('div', { class: 'actions' }, h('button', { type: 'button', class: 'btn sm', onclick: () => { try { videoFormFromSpec(JSON.parse(jsonTa.value)); buildVideoForm(); toast('shot list applied'); } catch (e) { toast('JSON: ' + e.message, 'err'); } } }, 'Apply JSON')));
  jsonBox.addEventListener('toggle', () => { if (jsonBox.open) jsonTa.value = JSON.stringify(Object.assign({ name: fs.name, width: Number(fs.width), height: Number(fs.height), fps: Number(fs.fps), transition: Number(fs.transition), audio: fs.audio?.path || null }, { shots: fs.shots.map(shotToSpec) }), null, 1); });
  const go = (mode, only) => {
    if (!fs.shots.length) return toast('add a shot', 'err');
    for (const s of fs.shots) { if (s.type !== 'logo' && !(s.prompt || '').trim()) return toast(`shot "${s.id}": write a prompt`, 'err'); if (s.type !== 'logo' && s.start?.mode === 'generate' && !(s.start.prompt || '').trim()) return toast(`shot "${s.id}": the start keyframe needs a prompt`, 'err'); if (s.type !== 'logo' && s.end?.mode === 'generate' && !(s.end.prompt || '').trim()) return toast(`shot "${s.id}": the end keyframe needs a prompt`, 'err'); }
    const ids = fs.shots.map(s => s.id); if (new Set(ids).size !== ids.length) return toast('shot ids must be unique', 'err');
    submit('video', videoParams(fs, mode, only));
  };
  fs.go = go;
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Shot list → video')),
    ...(info.models ? [] : [h('div', { class: 'errbox' }, 'Wan 2.2 weights not found: run ai-vid/setup.ps1 (~18 GB).')]),
    ...(info.ffmpeg ? [] : [h('div', { class: 'errbox' }, 'ffmpeg not found (PATH or C:\\ffmpeg\\bin): clips cannot be encoded or joined.')]),
    field('Preset', h('div', { class: 'row' }, presetSel, h('button', { type: 'button', class: 'btn sm', onclick: () => { const p = info.presets.find(x => x.file === presetSel.value); if (p) { videoFormFromSpec(JSON.parse(JSON.stringify(p.spec))); buildVideoForm(); toast('preset loaded'); } } }, 'Load')), 'Shot lists in ai-vid/shots/. Loading replaces the list below.'),
    h('div', { class: 'two' }, field('Project name', nameInp), field('Frame size', sizeSel)),
    h('div', { class: 'three' }, field('fps', num('fps', fs.fps, { min: 8, max: 60, oninput: e => fs.fps = e.target.value })), field('Cross-fade (s)', num('transition', fs.transition, { step: 0.1, min: 0, max: 3, oninput: e => fs.transition = e.target.value }), 'Only for shots that do not start on the previous frame; chained shots cut hard'), field('Transition', kindSel)),
    h('h3', { style: { margin: '6px 0 8px' } }, 'Shots'),
    h('div', { class: 'hint', style: { marginBottom: '8px' } }, 'Each video shot is a Wan 2.2 clip. "Previous" makes the shot continuous: it starts on the exact last frame of the shot before, no cross-fade, duplicate frame dropped. A generated keyframe is drawn with FLUX.2 klein from the prompt (and a reference picture as image 1), so the clip lands on exactly that picture. A logo shot animates a PNG (characters pop in, wordmark jumps out).'),
    shotsBox,
    h('div', { class: 'actions' }, h('button', { type: 'button', class: 'btn sm', onclick: () => { fs.shots.push(shotFromSpec({ id: `shot${fs.shots.length + 1}`, prompt: '', seconds: 3, start: fs.shots.length ? 'previous' : null }, fs.shots.length)); renderShots(); } }, '＋ Video shot'), h('button', { type: 'button', class: 'btn sm', onclick: () => { fs.shots.push(shotFromSpec({ id: 'reveal', type: 'logo', logo: 'assets/logo.png', seconds: 3 }, fs.shots.length)); renderShots(); } }, '＋ Logo reveal')),
    h('details', {}, h('summary', {}, 'Music / audio'), audioUploadBox('video', 'audio', 'Music or sting (optional)', 'Drop a wav/mp3 here or pick ♪ on a track in the Music library; it is cut to the video with a fade-out')),
    h('details', {}, h('summary', {}, 'Negative prompt'), field('Negative', textarea({ name: 'negative', placeholder: 'default: the Wan negative (blurry, static, subtitles, deformed …)', oninput: e => fs.negative = e.target.value }, fs.negative))),
    jsonBox,
    h('div', { class: 'actions' },
      h('button', { class: 'btn primary block', type: 'button', onclick: () => go('full') }, '▶ Generate all shots and assemble'),
      h('button', { class: 'btn block', type: 'button', onclick: () => go('keyframes') }, '🖼 Keyframes only (check them first)'),
      h('button', { class: 'btn block', type: 'button', onclick: () => go('assemble') }, '⧉ Assemble existing clips')),
    h('div', { class: 'hint' }, 'On the 2080: ~40 s of sampling per second of 480p video, ~2 min per keyframe with the base klein model. Shots are chained in order, so a change early on means regenerating what follows. Re-running with the same project name reuses existing keyframes and clips that are not regenerated.'));
  sizeSel.dispatchEvent(new Event('change'));
}
function frameEditor(f, label, i, which, fs) {
  // start/end frame of a video shot
  const box = h('div', { class: 'frame' });
  const render = () => {
    box.innerHTML = '';
    const sg = seg(`f${i}${which}`, [['none', 'None', 'Free: Wan decides'], ['previous', 'Previous', "Last frame of the shot before"], ['image', 'Picture', 'Upload / pick a frame'], ['generate', 'Generate', 'Draw a keyframe with FLUX.2 klein']], f.mode);
    sg.addEventListener('change', () => { f.mode = sg.dataset.value; if (f.mode === 'generate') { f.model = f.model || 'base'; f.prompt = f.prompt || ''; } render(); });
    box.append(h('div', { class: 'row', style: { alignItems: 'center', gap: '8px' } }, h('b', { class: 'small', style: { width: '40px' } }, label), sg));
    if (f.mode === 'image') box.append(uploadBoxFor(f, 'image', 'Frame picture', { pick: cb => pickFrame(cb) }));
    if (f.mode === 'generate') {
      box.append(textarea({ placeholder: 'the keyframe: what the picture shows (say "from image 1" for the reference)', style: { minHeight: '58px', marginTop: '6px' }, oninput: e => f.prompt = e.target.value }, f.prompt),
        uploadBoxFor(f, 'ref', 'Reference = image 1 (optional, e.g. the logo)', { pick: cb => pickFrame(cb) }),
        h('div', { class: 'row', style: { gap: '8px' } }, (() => { const m = seg(`m${i}${which}`, [['base', 'Base (20 steps)'], ['distilled', 'Distilled (4 steps)']], f.model || 'base'); m.addEventListener('change', () => f.model = m.dataset.value); return m; })(), num(`ks${i}${which}`, f.seed, { placeholder: 'seed', style: { width: '110px' }, oninput: e => f.seed = e.target.value === '' ? null : Number(e.target.value) })));
    }
  };
  render();
  return box;
}
function uploadBoxFor(obj, key, label, opts = {}) {
  // like uploadBox() but bound to an arbitrary object instead of state.forms[kind]
  const inp = h('input', { type: 'file', accept: 'image/*', class: 'hidden' });
  const box = h('div', { class: 'drop small' });
  const render = () => {
    const v = obj[key]; box.innerHTML = '';
    if (v) box.append(h('img', { src: v.url || '' , onerror: e => e.target.style.display = 'none' }), h('div', { class: 'txt' }, h('b', {}, v.name || basename(v.path)), h('br'), h('span', { class: 'dim' }, label)), h('span', { class: 'x', title: 'remove', onclick: e => { e.stopPropagation(); obj[key] = null; render(); } }, '✕'));
    else box.append(h('div', { class: 'txt' }, h('b', {}, label), h('br'), 'Drop a PNG or click', opts.pick ? h('span', {}, ' · ', h('a', { href: '#', onclick: e => { e.preventDefault(); e.stopPropagation(); opts.pick(v2 => { obj[key] = v2; render(); }); } }, 'pick')) : null));
  };
  box.onclick = () => inp.click();
  box.ondragover = e => { e.preventDefault(); box.classList.add('over'); };
  box.ondragleave = () => box.classList.remove('over');
  const take = async file => { if (!file) return; try { obj[key] = await api.upload(file); render(); } catch (e) { toast('upload failed: ' + e.message, 'err'); } };
  box.ondrop = e => { e.preventDefault(); box.classList.remove('over'); take(e.dataTransfer.files[0]); };
  inp.onchange = () => take(inp.files[0]);
  render();
  return h('div', { class: 'field', style: { marginTop: '6px' } }, box, inp);
}
function pickFrame(cb) {
  // keyframes, first/last frames of clips and sprites from the libraries
  Promise.all([loadLibrary('video'), loadLibrary('sprite')]).then(() => {
    const items = [];
    for (const v of state.library.video) { for (const k of v.keyframes || []) items.push({ url: k.url, name: `${v.name}/${k.file}`, path: `${v.path}\\${k.file}` }); for (const c of v.clips || []) { if (c.last) items.push({ url: c.last, name: `${v.name}/${c.id} last`, path: c.path.replace(/\.mp4$/, '_last.png') }); if (c.first) items.push({ url: c.first, name: `${v.name}/${c.id} first`, path: c.path.replace(/\.mp4$/, '_first.png') }); } }
    for (const it of state.library.sprite) items.push({ url: it.url, name: it.name, path: it.path });
    items.push({ url: '/files/vid-assets/logo.png', name: 'logo.png', path: 'assets/logo.png' });
    const grid = h('div', { class: 'grid', style: { width: '720px', maxWidth: '86vw' } });
    for (const it of items) grid.append(h('div', { class: 'tile', onclick: () => { cb({ path: it.path, url: it.url, name: basename(it.path) }); $('#modal').classList.add('hidden'); } }, h('div', { class: 'img' }, h('img', { src: it.url })), h('div', { class: 'cap' }, h('b', { title: it.name }, it.name))));
    modal(h('div', {}, h('h2', { style: { marginBottom: '12px' } }, 'Pick a picture'), grid));
  });
}
function shotCard(s, i, fs, rerender) {
  const card = h('div', { class: 'shot' });
  const move = d => { const j = i + d; if (j < 0 || j >= fs.shots.length) return; [fs.shots[i], fs.shots[j]] = [fs.shots[j], fs.shots[i]]; rerender(); };
  const typeSeg = seg(`t${i}`, [['video', 'Wan clip'], ['logo', 'Logo reveal']], s.type);
  typeSeg.addEventListener('change', () => { const t = typeSeg.dataset.value; Object.assign(s, shotFromSpec(Object.assign({ id: s.id, seconds: s.seconds, transition: s.transition }, t === 'logo' ? { type: 'logo' } : { prompt: s.prompt || '', start: 'previous' }), i)); rerender(); });
  card.append(h('div', { class: 'row shot-head' }, h('span', { class: 'num' }, i + 1), text(`id${i}`, s.id, { style: { width: '120px' }, title: 'shot id (file name)', oninput: e => s.id = e.target.value.replace(/[^A-Za-z0-9_\-]+/g, '_') }), typeSeg,
    h('span', { class: 'grow' }), h('button', { type: 'button', class: 'btn xs', title: 'move up', onclick: () => move(-1) }, '↑'), h('button', { type: 'button', class: 'btn xs', title: 'move down', onclick: () => move(1) }, '↓'),
    h('button', { type: 'button', class: 'btn xs', title: 'duplicate', onclick: () => { fs.shots.splice(i + 1, 0, shotFromSpec(Object.assign(shotToSpec(s), { id: s.id + '_2' }), i + 1)); rerender(); } }, '⧉'),
    h('button', { type: 'button', class: 'btn xs danger', title: 'remove', onclick: () => { fs.shots.splice(i, 1); rerender(); } }, '✕')));
  if (s.type === 'logo') {
    card.append(uploadBoxFor(s, 'logo', 'Logo PNG (transparent)', { pick: cb => pickFrame(cb) }),
      h('div', { class: 'three' }, field('Seconds', num(`sec${i}`, s.seconds, { step: 0.5, min: 1, oninput: e => s.seconds = e.target.value })), field('Wordmark split', num(`sp${i}`, s.split, { step: 0.01, min: 0.1, max: 0.95, oninput: e => s.split = e.target.value }), 'row where the text starts, 0–1 of the height'), field('Background', text(`bg${i}`, s.background, { oninput: e => s.background = e.target.value }))),
      h('div', { class: 'three' }, field('Logo width', num(`sc${i}`, s.scale, { step: 0.05, min: 0.2, max: 1, oninput: e => s.scale = e.target.value }), 'fraction of the frame'), field('Fade in from', select(`from${i}`, [['previous', 'previous shot\u2019s last frame'], ['', 'nothing (background)']], s.from || 'previous'), ''), field('Cross-fade in (s)', num(`tr${i}`, s.transition, { step: 0.1, min: 0, placeholder: 'default', oninput: e => s.transition = e.target.value }), 'ignored when fading in from the previous frame')));
    card.querySelector(`select[name="from${i}"]`).onchange = e => s.from = e.target.value;
  } else {
    card.append(textarea({ placeholder: 'what happens in the shot: subject, motion, camera, style ("flat 2D cartoon with thick black ink outlines", "static camera")', style: { minHeight: '70px' }, oninput: e => s.prompt = e.target.value }, s.prompt),
      h('div', { class: 'three' }, field('Seconds', num(`sec${i}`, s.seconds, { step: 0.5, min: 1, max: 8, oninput: e => s.seconds = e.target.value }), '4k+1 frames at 24 fps'), field('Seed', num(`seed${i}`, s.seed, { placeholder: 'random', oninput: e => s.seed = e.target.value === '' ? null : Number(e.target.value) })), field('Cross-fade in (s)', num(`tr${i}`, s.transition, { step: 0.1, min: 0, placeholder: 'default', oninput: e => s.transition = e.target.value }), 'ignored when Start = Previous (continuous)')),
      frameEditor(s.start, 'Start', i, 's', fs), frameEditor(s.end, 'End', i, 'e', fs));
  }
  card.append(h('div', { class: 'row', style: { marginTop: '8px', justifyContent: 'flex-end' } }, h('button', { type: 'button', class: 'btn xs', title: 'Regenerate only this shot (the others are reused from the project folder), then re-assemble', onclick: () => fs.go('full', [s.id]) }, '↻ Regenerate only this shot')));
  return card;
}
function textarea(attrs, value) { const t = h('textarea', attrs); t.value = value || ''; return t; }
function videoEl(url, attrs = {}) { return h('video', Object.assign({ src: url, controls: true, preload: 'metadata', playsinline: true }, attrs)); }
function resultVideo(job, lib) {
  const o = job?.outputs || {};
  const finalUrl = o.final?.url || lib?.final;
  const clips = o.clips?.length ? o.clips : (lib?.clips || []);
  const keyframes = o.keyframes?.length ? o.keyframes : (lib?.keyframes || []).map(k => ({ url: k.url, shot: k.file }));
  const p = job?.params || lib?.job?.params || null;
  const box = h('div', {});
  if (finalUrl) box.append(h('div', { class: 'big video' }, videoEl(finalUrl, { loop: true, autoplay: true, muted: true })), h('div', { class: 'actions' }, dl(finalUrl, 'MP4'), reveal(o.final?.path || lib?.final_path), p ? h('button', { class: 'btn sm', onclick: () => reuse(Object.assign({ kind: 'video', params: p }, job || {})) }, '↻ Load shot list into the form') : null));
  else if (job?.meta?.mode === 'keyframes') box.append(h('div', { class: 'muted' }, 'Keyframes only: check the pictures below, then press "Generate all shots" (the keyframes are reused).'));
  else box.append(h('div', { class: 'empty' }, 'No video yet'));
  if (clips.length) {
    box.append(h('h3', { style: { margin: '12px 0 6px' } }, 'Shots'));
    const row = h('div', { class: 'clips' });
    for (const c of clips) row.append(h('div', { class: 'c' }, c.url ? videoEl(c.url, { loop: true, muted: true, onmouseenter: e => e.target.play(), onmouseleave: e => e.target.pause() }) : (c.first ? h('img', { src: c.first }) : null),
      h('div', { class: 'small' }, h('b', {}, c.id), c.seed != null ? h('span', { class: 'dim' }, ` · seed ${c.seed}`) : null, c.seconds ? h('span', { class: 'dim' }, ` · ${c.seconds} s`) : null, c.reused ? h('span', { class: 'dim' }, ' · reused') : null),
      h('div', { class: 'row', style: { gap: '4px' } }, c.last ? h('button', { class: 'btn xs', title: 'last frame', onclick: () => lightbox(c.last) }, 'last frame') : null, p ? h('button', { class: 'btn xs', title: 'Regenerate this shot with the same settings (new seed) and re-assemble', onclick: () => submit('video', Object.assign({}, p, { mode: 'full', only: [c.id], shots: p.shots.map(s => s.id === c.id ? Object.assign({}, s, { seed: null }) : s) })) }, '↻ redo') : null)));
    box.append(row);
  }
  if (keyframes.length) box.append(h('h3', { style: { margin: '12px 0 6px' } }, 'Keyframes'), thumbStrip(keyframes.map(k => ({ label: `${k.shot} ${k.which || ''}`.trim(), kind: 'image', url: k.url })), it => lightbox(it.url)));
  if (p) box.append(kv([['Project', p.name], ['Size', `${p.width}×${p.height} @ ${p.fps} fps`], ['Shots', (p.shots || []).map(s => s.id).join(' → ')], ['Audio', p.audio_upload ? basename(p.audio_upload) : null], ['Mode', job?.meta?.mode !== 'full' ? job?.meta?.mode : null]]));
  return box;
}
function videoTable(items) {
  const wrap = h('div', { class: 'stack' });
  for (const it of items) {
    const job = it.job ? Object.assign({ kind: 'video', status: 'done', id: it.job.id }, it.job) : null;
    const sel = state.selected.video === it.path;
    const row = h('div', { class: 'vrow' + (sel ? ' sel' : ''), onclick: () => { state.selected.video = sel ? null : it.path; renderFlow('video'); } },
      it.poster ? h('img', { src: it.poster }) : h('span', { class: 'ph' }, '▶'),
      h('div', { class: 'grow' }, h('b', {}, it.name), h('div', { class: 'dim small' }, `${it.clips.length} clip${it.clips.length === 1 ? '' : 's'} · ${fmtDate(it.mtime)}${it.final ? '' : ' · not assembled'}`)),
      it.final ? dl(it.final, 'MP4') : null,
      h('button', { class: 'btn xs', title: 'Load this shot list into the form', onclick: e => { e.stopPropagation(); reuse({ kind: 'video', params: job ? job.params : specToParams(it.spec) }); } }, '↻'));
    wrap.append(row);
    if (sel) wrap.append(h('div', { class: 'detail' }, resultVideo(job, Object.assign({ kind: 'video' }, it))));
  }
  return wrap;
}
function specToParams(spec) { const fs = {}; const f = Object.assign({}, state.forms.video); videoFormFromSpec(spec || {}); const p = videoParams(state.forms.video, 'full', []); state.forms.video = f; return p; }

// ------------------------------------------------------------------ animate (ai-anim: Make-It-Animatable + Kimodo)
function buildAnimFormShell() { return h('form', { class: 'card form', id: 'anim-form', onsubmit: e => e.preventDefault() }, h('div', { class: 'empty' }, 'loading…')); }
function modelBox(fs, key, label, pickTitle) {
  // source model for rigging: a GLB/FBX/OBJ upload, or a GLB picked from the 3D library (kept as a /files/ URL)
  const inp = h('input', { type: 'file', accept: '.glb,.gltf,.fbx,.obj', class: 'hidden' });
  const box = h('div', { class: 'drop' });
  const render = () => {
    const v = fs[key]; box.innerHTML = '';
    if (v) box.append(v.thumb ? h('img', { src: v.thumb }) : h('span', { class: 'ph' }, '⬡'), h('div', { class: 'txt' }, h('b', {}, v.name || basename(v.path || v.url)), h('br'), h('span', { class: 'dim' }, v.path ? 'uploaded file' : 'from the 3D library')), h('span', { class: 'x', title: 'remove', onclick: e => { e.stopPropagation(); delete fs[key]; render(); } }, '✕'));
    else box.append(h('div', { class: 'txt' }, h('b', {}, label), h('br'), 'Drop a GLB (or FBX / OBJ) here, click to choose, or ', h('a', { href: '#', onclick: e => { e.preventDefault(); e.stopPropagation(); pickModel(v2 => { fs[key] = v2; render(); }, pickTitle); } }, 'pick from the 3D library')));
  };
  box.onclick = () => inp.click();
  box.ondragover = e => { e.preventDefault(); box.classList.add('over'); };
  box.ondragleave = () => box.classList.remove('over');
  const take = async file => { if (!file) return; try { const u = await api.upload(file); fs[key] = u; render(); } catch (e) { toast('upload failed: ' + e.message, 'err'); } };
  box.ondrop = e => { e.preventDefault(); box.classList.remove('over'); take(e.dataTransfer.files[0]); };
  inp.onchange = () => take(inp.files[0]);
  render();
  const wrap = h('div', { class: 'field' }, box, inp);
  wrap.refresh = render;
  return wrap;
}
function pickModel(cb, title = 'Pick a 3D model to rig') {
  const items = (state.library.model3d || []).filter(it => it.glb);
  const grid = h('div', { class: 'grid' });
  for (const it of items) grid.append(h('div', { class: 'tile', onclick: () => { cb({ url: it.glb, name: it.name, thumb: it.thumb || it.reference || it.diffuse }); $('#modal').classList.add('hidden'); } },
    h('div', { class: 'img' }, (it.thumb || it.reference || it.diffuse) ? h('img', { src: it.thumb || it.reference || it.diffuse, loading: 'lazy' }) : h('span', { class: 'ph' }, '⬡')), h('div', { class: 'cap' }, h('b', {}, it.name), h('span', {}, fmtDate(it.mtime)))));
  modal(h('div', { style: { width: '70vw', maxHeight: '75vh', overflow: 'auto' } }, h('h3', { style: { marginBottom: '10px' } }, title), items.length ? grid : h('div', { class: 'empty' }, 'No models in the 3D library yet: generate one with the Character preset on the 3D Models page')));
}
const CLIP_PRESETS = [['Idle', 'A person stands still, breathing calmly, shifting their weight a little and looking around.'], ['Walk', 'A person walks forward at a relaxed, steady pace.'], ['Run', 'A person runs forward.'], ['Wave', 'A person waves hello with their right hand.'], ['Jump', 'A person jumps up in place and lands.'], ['Sit', 'A person sits down on a chair and stays seated.'], ['Talk', 'A person stands and talks, gesturing with both hands.'], ['Dance', 'A person dances happily.']];
function buildAnimForm() {
  const form = $('#anim-form'); if (!form) return;
  const fs = state.forms.anim = state.forms.anim || {};
  if (!fs.clips) fs.clips = [{ name: 'Idle', prompt: CLIP_PRESETS[0][1], seconds: 4 }, { name: 'Walk', prompt: CLIP_PRESETS[1][1], seconds: 4 }];
  const vals = fs.values || {};
  const rd = state.anim?.ready || {};
  form.innerHTML = '';
  const warn = [];
  if (!rd.venv) warn.push('ai-anim venv missing: run ai-anim\\setup.ps1');
  if (!rd.mia_weights) warn.push('Make-It-Animatable weights missing (setup.ps1 downloads them)');
  if (!rd.blender) warn.push('Blender 5.x not found (set the BLENDER env var)');
  if (!rd.hf_token) warn.push("No Hugging Face token (~/.cache/huggingface/token): Kimodo's Llama-3 text encoder is gated. Request access to meta-llama/Meta-Llama-3-8B-Instruct on huggingface.co, then run \"hf auth login\". Rigging works without it.");
  const clipsEl = h('div', { class: 'clips' });
  const renderClips = () => {
    clipsEl.innerHTML = '';
    fs.clips.forEach((c, i) => {
      const row = h('div', { class: 'clip' },
        h('input', { type: 'text', value: c.name || '', placeholder: 'Idle', title: 'clip name (Unity state)', oninput: e => c.name = e.target.value }),
        h('input', { type: 'text', value: c.prompt || '', placeholder: 'A person …', title: 'what the person does', oninput: e => c.prompt = e.target.value }),
        h('input', { type: 'number', value: c.seconds ?? 4, step: 0.5, min: 1, max: 20, title: 'seconds', oninput: e => c.seconds = Number(e.target.value) || null }),
        h('input', { type: 'number', value: c.seed ?? '', placeholder: 'seed', title: 'seed (empty = random)', oninput: e => c.seed = e.target.value === '' ? null : Number(e.target.value) }),
        h('span', { class: 'x', title: 'remove', onclick: () => { fs.clips.splice(i, 1); renderClips(); } }, '✕'));
      clipsEl.append(row);
    });
    clipsEl.append(h('div', { class: 'row', style: { gap: '6px', flexWrap: 'wrap' } },
      h('button', { type: 'button', class: 'btn sm', onclick: () => { fs.clips.push({ name: '', prompt: '', seconds: 4 }); renderClips(); } }, '+ clip'),
      ...CLIP_PRESETS.map(([n, p]) => h('button', { type: 'button', class: 'btn xs', title: p, onclick: () => { fs.clips.push({ name: n, prompt: p, seconds: n === 'Idle' || n === 'Talk' ? 5 : 4 }); renderClips(); } }, n))));
  };
  renderClips();
  form.append(h('div', { class: 'card-head' }, h('h2', {}, 'Rig + animate a character')),
    ...(warn.length ? [h('div', { class: 'errbox' }, ...warn.map(w => h('div', {}, w)))] : []),
    modelBox(fs, 'source', 'Character mesh'),
    h('div', { class: 'two' }, field('Character name', text('name', vals.name || '', { placeholder: 'Hero (from the model)' })), field('Height (m)', num('height', vals.height ?? 1.8, { step: 0.05, min: 0.2 }))),
    check('no_fingers', 'No finger bones (hands only; for mitten-like low-poly hands)', !!vals.no_fingers),
    check('keep_pose', 'Keep the input pose as rest pose (default: move it into the Mixamo T-pose)', !!vals.keep_pose),
    field('Animation clips', clipsEl, 'One Kimodo clip per row: a name (becomes the glTF animation / FBX take) and what the person does. Several sentences separated by " | " are played in sequence inside one clip.'),
    h('div', { class: 'two' }, field('Default seconds', num('seconds', vals.seconds ?? 4, { step: 0.5, min: 1, max: 20 })), seedField()),
    check('inplace', 'In place: remove the horizontal root motion (character animates on the spot)', !!vals.inplace),
    h('details', {}, h('summary', {}, 'Advanced'), field('Diffusion steps', num('steps', vals.steps ?? 100, { step: 10, min: 10, max: 200 }), 'Kimodo denoising steps; 100 is the default, 50 is ~2x faster')),
    h('div', { class: 'actions' },
      h('button', { class: 'btn primary block', type: 'button', onclick: () => run('full') }, '▶ Rig + generate the clips'),
      h('button', { class: 'btn block', type: 'button', onclick: () => run('rig') }, '1 · Rig only (check the T-pose first)')),
    h('div', { class: 'hint' }, 'Rigging takes ~10 s (Make-It-Animatable + Blender). Each clip ~10–30 s once the Llama-3 text encoder is loaded (first load: a minute or two, 16 GB RAM). Result: <Name>.glb with every clip as a glTF animation, <Name>.fbx with one take per clip (Mixamo bone names: Unity Humanoid maps them automatically).'));
  if (state.anim?.tips) form.append(tipsBlock(state.anim.tips));
  const run = stage => {
    const p = readForm(form); p.stage = stage;
    if (!fs.source) return toast('pick a character mesh (upload, or from the 3D library)', 'err');
    if (fs.source.path) { p.mesh_upload = fs.source.path; p.mesh_url = fs.source.url; } else { p.source_url = fs.source.url; p.source_name = fs.source.name; }
    if (!p.name) p.name = (fs.source.name || basename(fs.source.path || fs.source.url)).replace(/\.[a-z0-9]+$/i, '');
    p.clips = fs.clips.filter(c => (c.prompt || '').trim()).map(c => ({ name: c.name, prompt: c.prompt, seconds: c.seconds, seed: c.seed }));
    if (stage === 'full' && !p.clips.length) return toast('add at least one clip, or use Rig only', 'err');
    submit('anim', p);
  };
}
function tipsBlock(tips) {
  const mk = list => h('table', {}, ...list.map(t => h('tr', {}, h('td', {}, h('code', {}, t.mark)), h('td', {}, t.effect))));
  return h('details', { class: 'tips', style: { marginTop: '10px' } }, h('summary', {}, 'Prompt tips'), mk(tips.documented || []), tips.untested?.length ? h('div', {}, h('div', { class: 'dim small', style: { margin: '8px 0 4px' } }, 'Untested ideas'), mk(tips.untested)) : null, tips.note ? h('div', { class: 'hint' }, tips.note) : null);
}
function resultAnim(job, lib) {
  const o = (job && job.outputs) || {};
  const glb = o.glb?.url || lib?.glb;
  const clips = (o.clips || lib?.clips || []);
  const box = h('div', {});
  if (!glb) { box.append(h('div', { class: 'empty' }, 'No files yet')); return box; }
  const sel = h('select', {});
  const playBtnEl = h('button', { class: 'btn sm', type: 'button' }, '▶ Play');
  const info = h('span', { class: 'dim small' }, '');
  const bar = h('div', { class: 'anim-bar' }, h('span', { class: 'dim small' }, 'Clip'), sel, playBtnEl, info);
  let viewer = null, playing = false;
  const play = () => { if (!viewer) return; const c = viewer.playClip(sel.value); playing = !!c; playBtnEl.textContent = playing ? '■ Stop' : '▶ Play'; info.textContent = c ? `${c.duration.toFixed(1)} s` : ''; };
  const stop = () => { if (viewer) viewer.stopClip(); playing = false; playBtnEl.textContent = '▶ Play'; };
  playBtnEl.onclick = () => playing ? stop() : play();
  sel.onchange = () => { if (playing) play(); };
  const vb = viewerBox(glb + '?t=' + Math.floor((job?.finished || lib?.mtime || 0)), (v, inf) => {
    viewer = v; sel.innerHTML = '';
    for (const c of inf.clips) sel.append(h('option', { value: c.name }, `${c.name} (${c.duration.toFixed(1)} s)`));
    if (inf.clips.length) play(); else bar.classList.add('hidden');
  });
  box.append(vb, bar);
  box.append(h('div', { class: 'actions' }, dl(glb, 'GLB (with clips)'), dl(o.fbx?.url || lib?.fbx, 'FBX (Unity)'), dl(o.diffuse?.url || lib?.diffuse, 'Diffuse PNG'), reveal(o.glb?.path || lib?.path)));
  if (clips.length) box.append(h('table', { class: 'tbl' }, h('tr', {}, h('th', {}, 'Clip'), h('th', {}, 'Prompt'), h('th', {}, 's'), h('th', {}, 'Seed'), h('th', {}, '')),
    ...clips.map(c => h('tr', {}, h('td', {}, h('b', {}, c.name)), h('td', {}, c.prompt || ''), h('td', {}, c.seconds ?? ''), h('td', {}, c.seed ?? ''), h('td', {}, c.bvh ? dl(c.bvh, 'BVH') : null)))));
  const m = job?.meta || lib?.job?.meta || {};
  const p = job?.params || lib?.job?.params || {};
  const name = m.name || lib?.name || p.name;
  const height = p.height ?? lib?.height;
  box.append(kv([['Name', name], ['Height', height && `${height} m`], ['Rig', m.rig_stats || lib?.rig_stats], ['Root motion', (p.inplace ?? lib?.inplace) ? 'in place' : 'kept (hips move)'], ['Source', p.source_name || (p.mesh_upload && basename(p.mesh_upload)) || (lib?.source && basename(lib.source))]]));
  if (name) box.append(h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: () => {
    const fs = state.forms.anim || {}; const form = $('#anim-form'); const pf = form ? readForm(form) : {};
    const cl = (fs.clips || []).filter(c => (c.prompt || '').trim()).map(c => ({ name: c.name, prompt: c.prompt, seconds: c.seconds, seed: c.seed }));
    if (!cl.length) return toast('add clips in the form first', 'err');
    submit('anim', { stage: 'anim', name, clips: cl, seconds: pf.seconds, seed: pf.seed, steps: pf.steps, inplace: pf.inplace });
  } }, '＋ Add the clips from the form to this character'), h('span', { class: 'hint' }, 'Generates only the new clips, keeps the existing ones, re-exports the GLB and FBX.')));
  return box;
}
