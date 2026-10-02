// J.A.R.V.I.S. viewer -- the galaxy, the voice and the magic.
// GRAPH comes from graph-data.js (written by build.py / server.py).
import * as THREE from 'three';
import ForceGraph3D from '3d-force-graph';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';

const $ = (sel) => document.querySelector(sel);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const REMEMBER_RE = /^\s*(?:(?:hey\s+)?jarvis[\s,.!:]*)?(?:please\s+)?remember\b/i;
const SESSION = (() => {
  try {
    let s = sessionStorage.getItem('jarvis-session');
    if (!s) { s = crypto.randomUUID(); sessionStorage.setItem('jarvis-session', s); }
    return s;
  } catch { return Math.random().toString(36).slice(2); }
})();

const prefs = {
  get(key, fallback) { try { const v = localStorage.getItem('jarvis-' + key); return v === null ? fallback : v === '1'; } catch { return fallback; } },
  set(key, val) { try { localStorage.setItem('jarvis-' + key, val ? '1' : '0'); } catch { /* private mode */ } },
};

// ------------------------------------------------------------------ data & colours

const PALETTE = ['#4cc9f0', '#f72585', '#ffd166', '#06d6a0', '#b388ff', '#ff8c42', '#72efdd', '#ff5d73', '#9ef01a', '#7b8cff', '#f4a261', '#48bfe3'];
const groups = [...new Set(GRAPH.nodes.map((n) => n.group))].sort();
function groupColor(g) {
  if (!groups.includes(g)) groups.push(g);
  return PALETTE[groups.indexOf(g) % PALETTE.length];
}

const adj = new Map();
const endId = (end) => (typeof end === 'object' ? end.id : end);
function addEdge(l) {
  const a = endId(l.source), b = endId(l.target);
  if (!adj.has(a)) adj.set(a, new Set());
  if (!adj.has(b)) adj.set(b, new Set());
  adj.get(a).add(b); adj.get(b).add(a);
}
GRAPH.links.forEach(addEdge);

const status = { count: GRAPH.meta.count, title: 'sir', brain: '', backend: 'offline' };

// ------------------------------------------------------------------ textures & node objects

function makeGlowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.18, 'rgba(255,255,255,0.55)');
  grad.addColorStop(0.45, 'rgba(255,255,255,0.12)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}
const glowTex = makeGlowTexture();
const sphereGeo = new THREE.SphereGeometry(1, 24, 24);

function makeLabel(text) {
  const c = document.createElement('canvas');
  const g = c.getContext('2d');
  const font = '600 44px Rajdhani, "Segoe UI", sans-serif';
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 24;
  c.width = w; c.height = 60;
  g.font = font;
  g.textBaseline = 'middle';
  g.shadowColor = 'rgba(0,0,0,0.9)';
  g.shadowBlur = 8;
  g.fillStyle = '#e6f3ff';
  g.fillText(text, 12, 31);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, opacity: 0.8 });
  const sprite = new THREE.Sprite(mat);
  const h = 4.2;
  sprite.scale.set(h * (w / 60), h, 1);
  return sprite;
}

const nodeRadius = (n) => 2.4 + Math.sqrt(n.degree || 0) * 1.25;
const SHOW_ALL_LABELS = GRAPH.nodes.length <= 90;

function makeNodeObject(n) {
  const color = new THREE.Color(groupColor(n.group));
  const r = nodeRadius(n);
  const group = new THREE.Group();
  const core = new THREE.Mesh(sphereGeo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95 }));
  core.scale.setScalar(r);
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTex, color, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  glow.scale.setScalar(r * 7);
  const label = makeLabel(n.label);
  label.position.set(0, r + 4.5, 0);
  label.visible = SHOW_ALL_LABELS;
  group.add(glow, core, label);
  n.__parts = { core, glow, label, r, glowBase: r * 7 };
  return group;
}

// ------------------------------------------------------------------ the galaxy

const hl = { nodes: new Set(), links: new Set(), focus: null };
let hoverNode = null;

const linkColor = (l) => (hl.links.has(l) ? 'rgba(150,230,255,0.95)' : hl.nodes.size ? 'rgba(110,140,190,0.06)' : 'rgba(130,165,215,0.26)');
const linkWidth = (l) => (hl.links.has(l) ? 1.1 : 0);
const linkParticles = (l) => (hl.links.has(l) ? 3 : 0);

const Graph = new ForceGraph3D(document.getElementById('graph'), { controlType: 'orbit' })
  .backgroundColor('#02040a')
  .showNavInfo(false)
  .nodeThreeObject(makeNodeObject)
  .nodeLabel((n) => (SHOW_ALL_LABELS ? '' : `${escapeHtml(n.label)}`))
  .linkColor(linkColor)
  .linkWidth(linkWidth)
  .linkOpacity(1)
  .linkDirectionalParticles(linkParticles)
  .linkDirectionalParticleWidth(1.6)
  .linkDirectionalParticleSpeed(0.007)
  .linkDirectionalParticleColor(() => '#bfefff')
  .d3VelocityDecay(0.3)
  .warmupTicks(60)
  .cooldownTime(8000)
  .onNodeClick((n) => { stopSpeakingIfBusy(); focusNode(n.id); })
  .onNodeHover((n) => {
    hoverNode = n || null;
    document.getElementById('graph').style.cursor = n ? 'pointer' : '';
    refreshStyles();
  })
  .onBackgroundClick(() => clearFocus())
  .graphData(GRAPH);

Graph.d3Force('charge').strength(-140);
Graph.d3Force('link').distance(48);

// Bloom: everything bright gets a soft halo.
const bloom = new UnrealBloomPass(new THREE.Vector2(Math.max(innerWidth, 1), Math.max(innerHeight, 1)), 1.05, 0.6, 0.12);
Graph.postProcessingComposer().addPass(bloom);

// Deep-space backdrop: two star layers and a few faint nebulae.
const sky = new THREE.Group();
function starLayer(count, size, rMin, rMax, opacity) {
  const pos = new Float32Array(count * 3);
  const col = new Float32Array(count * 3);
  const tint = [new THREE.Color('#ffffff'), new THREE.Color('#bcd7ff'), new THREE.Color('#ffe7c4'), new THREE.Color('#9fd8ff')];
  for (let i = 0; i < count; i++) {
    const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2;
    const r = rMin + Math.random() * (rMax - rMin), s = Math.sqrt(1 - u * u);
    pos.set([r * s * Math.cos(th), r * u, r * s * Math.sin(th)], i * 3);
    const c = tint[(Math.random() * tint.length) | 0];
    col.set([c.r, c.g, c.b], i * 3);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  return new THREE.Points(geo, new THREE.PointsMaterial({
    size, sizeAttenuation: false, vertexColors: true, transparent: true, opacity, depthWrite: false,
  }));
}
sky.add(starLayer(5200, 1.3, 1800, 4200, 0.75), starLayer(500, 2.6, 1800, 4200, 0.95));
[['#1b4b8f', -2600, 900, -2400], ['#5b1f73', 2400, -700, -2000], ['#0f5e6e', 300, 1600, 2800], ['#3a2a7a', -1800, -1500, 2200]]
  .forEach(([color, x, y, z]) => {
    const neb = new THREE.Sprite(new THREE.SpriteMaterial({
      map: glowTex, color: new THREE.Color(color), transparent: true, opacity: 0.22, blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    neb.position.set(x, y, z);
    neb.scale.setScalar(3400);
    sky.add(neb);
  });
Graph.scene().add(sky);

addEventListener('resize', () => {
  if (!innerWidth || !innerHeight) return; // hidden tab / minimised window
  Graph.width(innerWidth).height(innerHeight);
  bloom.setSize(innerWidth, innerHeight);
});

// ------------------------------------------------------------------ highlight / focus

function setHighlight(ids, focusId = null) {
  hl.nodes = new Set(ids);
  hl.focus = focusId;
  hl.links = new Set(Graph.graphData().links.filter((l) => hl.nodes.has(endId(l.source)) && hl.nodes.has(endId(l.target))));
  refreshStyles();
}

function refreshStyles() {
  const active = hl.nodes.size > 0;
  for (const n of Graph.graphData().nodes) {
    const p = n.__parts;
    if (!p) continue;
    const on = !active || hl.nodes.has(n.id);
    const isFocus = n.id === hl.focus;
    const hovered = hoverNode && hoverNode.id === n.id;
    p.core.material.opacity = on ? 0.95 : 0.1;
    p.glow.material.opacity = on ? (isFocus ? 0.8 : 0.55) : 0.035;
    p.glowBase = p.r * (isFocus ? 9 : 7);
    p.label.visible = SHOW_ALL_LABELS || hovered || (active && on);
    p.label.material.opacity = hovered || isFocus ? 1 : on ? (active ? 0.92 : 0.72) : 0.07;
  }
  Graph.linkColor(linkColor).linkWidth(linkWidth).linkDirectionalParticles(linkParticles);
}

const nodeById = (id) => Graph.graphData().nodes[id];

function flyTo(node, ms = 1800, dist = 120) {
  const r = Math.hypot(node.x, node.y, node.z) || 1;
  const k = 1 + dist / r;
  Graph.cameraPosition({ x: node.x * k, y: node.y * k + 8, z: node.z * k }, { x: node.x, y: node.y, z: node.z }, ms);
}

function focusNode(id, { extra = [], fly = true, panel = true } = {}) {
  const node = nodeById(id);
  if (!node) return;
  setHighlight([id, ...(adj.get(id) || []), ...extra], id);
  if (fly) flyTo(node);
  if (panel) showPanel(node);
  pauseDrift();
}

function focusCluster(ids) {
  const set = new Set(ids);
  setHighlight(set, ids[0]);
  Graph.zoomToFit(1800, 90, (n) => set.has(n.id));
  hidePanel();
  pauseDrift();
}

function clearFocus() {
  setHighlight([]);
  hidePanel();
  document.querySelectorAll('#legend-groups button').forEach((b) => b.classList.remove('active'));
}

function overview() {
  clearFocus();
  Graph.zoomToFit(1400, 60);
}

// ------------------------------------------------------------------ idle drift

const controls = Graph.controls();
controls.autoRotateSpeed = 0.45;
controls.enableDamping = true;
controls.dampingFactor = 0.08;
let driftEnabled = prefs.get('drift', true);
let driftTimer = null;
function pauseDrift() {
  controls.autoRotate = false;
  clearTimeout(driftTimer);
  if (driftEnabled) driftTimer = setTimeout(() => { controls.autoRotate = true; }, 25000);
}
controls.addEventListener('start', pauseDrift);
controls.autoRotate = driftEnabled;

// ------------------------------------------------------------------ note panel

function renderExcerpt(text) {
  const html = escapeHtml(text).replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g,
    (_, target, alias) => `<button class="wl" type="button" data-title="${target.trim()}">${(alias || target).trim()}</button>`);
  return html.split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
}

function showPanel(node) {
  $('#panel-group').innerHTML = `<i style="background:${groupColor(node.group)}"></i>${escapeHtml(node.group)}`;
  $('#panel-title').textContent = node.label;
  $('#panel-path').textContent = node.path;
  $('#panel-body').innerHTML = renderExcerpt(node.excerpt);
  const neighbours = [...(adj.get(node.id) || [])].map(nodeById).filter(Boolean).sort((a, b) => b.degree - a.degree);
  $('#panel-links').innerHTML = neighbours.length
    ? neighbours.map((n) => `<button type="button" data-id="${n.id}"><i style="background:${groupColor(n.group)}"></i>${escapeHtml(n.label)}</button>`).join('')
    : '<span class="panel-path">No connections yet.</span>';
  $('#panel').classList.add('open');
  $('#panel').setAttribute('aria-hidden', 'false');
  document.body.classList.add('panel-open');
  $('#panel').scrollTop = 0;
}

function hidePanel() {
  $('#panel').classList.remove('open');
  $('#panel').setAttribute('aria-hidden', 'true');
  document.body.classList.remove('panel-open');
}

$('#panel-close').addEventListener('click', () => clearFocus());
$('#panel').addEventListener('click', (e) => {
  const wl = e.target.closest('.wl');
  if (wl) {
    const t = wl.dataset.title.toLowerCase();
    const n = Graph.graphData().nodes.find((x) => x.label.toLowerCase() === t);
    return n ? focusNode(n.id) : toast(`No note called “${wl.dataset.title}” yet.`);
  }
  const b = e.target.closest('button[data-id]');
  if (b) focusNode(Number(b.dataset.id));
});

// ------------------------------------------------------------------ legend

function renderLegend() {
  const counts = {};
  Graph.graphData().nodes.forEach((n) => { counts[n.group] = (counts[n.group] || 0) + 1; });
  $('#legend-groups').innerHTML = groups.filter((g) => counts[g]).map((g) =>
    `<li><button type="button" data-group="${escapeHtml(g)}"><span class="swatch" style="background:${groupColor(g)};box-shadow:0 0 8px ${groupColor(g)}"></span>${escapeHtml(g)}<span class="count">${counts[g]}</span></button></li>`).join('');
}
$('#legend-groups').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-group]');
  if (!b) return;
  const wasActive = b.classList.contains('active');
  document.querySelectorAll('#legend-groups button').forEach((x) => x.classList.remove('active'));
  if (wasActive) return overview();
  b.classList.add('active');
  const ids = Graph.graphData().nodes.filter((n) => n.group === b.dataset.group).map((n) => n.id);
  focusCluster(ids);
});

// ------------------------------------------------------------------ HUD

function updateStats() {
  $('#stat-notes').textContent = Graph.graphData().nodes.length;
  $('#stat-links').textContent = Graph.graphData().links.length;
}
function tickClock() {
  $('#stat-clock').textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
setInterval(tickClock, 10000);

let toastTimer;
function toast(msg, ms = 4200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms);
}

// ------------------------------------------------------------------ state (drives reactor + status line)

let state = 'idle';
let speechLevel = 0;
const STATUS_TEXT = { listening: '● listening…', thinking: '● thinking…', speaking: '● speaking', filing: '● filing that away…' };
function setState(s, label) {
  state = s === 'filing' ? 'thinking' : s;
  document.body.dataset.state = state;
  $('#status').textContent = label ?? STATUS_TEXT[s] ?? '';
  $('#mic').classList.toggle('live', s === 'listening');
}

// ------------------------------------------------------------------ voice out (speechSynthesis)

const Voice = {
  enabled: prefs.get('voice', true),
  voice: null,
  speaking: false,
  token: 0,
  pick() {
    if (!('speechSynthesis' in window)) return null;
    const vs = speechSynthesis.getVoices();
    const gb = vs.filter((v) => /en[-_]GB/i.test(v.lang));
    const prefer = [/Google UK English Male/i, /Ryan/i, /George/i, /Thomas/i, /Arthur/i, /Daniel/i, /Oliver/i, /Male/i];
    for (const p of prefer) {
      const v = gb.find((x) => p.test(x.name));
      if (v) return (this.voice = v);
    }
    return (this.voice = gb[0] || vs.find((v) => /^en/i.test(v.lang)) || vs[0] || null);
  },
  async speak(text) {
    if (!this.enabled || !('speechSynthesis' in window) || !text) return;
    const token = ++this.token;
    speechSynthesis.cancel();
    await sleep(60);
    if (!this.voice) this.pick();
    // Chrome cuts off utterances longer than ~15s, so speak sentence by sentence.
    const chunks = text.match(/[^.!?;]+[.!?;]+["')\]]*|[^.!?;]+$/g) || [text];
    this.speaking = true;
    Wake.pause();
    setState('speaking');
    for (const chunk of chunks) {
      if (token !== this.token) return;
      await new Promise((resolve) => {
        const u = new SpeechSynthesisUtterance(chunk.trim());
        if (this.voice) u.voice = this.voice;
        u.lang = this.voice?.lang || 'en-GB';
        u.rate = 1.02;
        u.pitch = 0.92;
        u.onstart = () => { speechLevel = 1; };
        u.onboundary = () => { speechLevel = Math.min(1, speechLevel + 0.55); };
        u.onend = u.onerror = () => resolve();
        speechSynthesis.speak(u);
      });
    }
    if (token === this.token) this.finish();
  },
  stop() {
    this.token++;
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    if (this.speaking) this.finish();
  },
  finish() {
    this.speaking = false;
    if (state === 'speaking') setState('idle');
    Wake.resume();
  },
};
if ('speechSynthesis' in window) speechSynthesis.onvoiceschanged = () => Voice.pick();

function stopSpeakingIfBusy() { if (Voice.speaking) Voice.stop(); }

// ------------------------------------------------------------------ voice in (webkitSpeechRecognition)

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let pttRec = null;

function startListening() {
  if (!SR) return toast('Voice input needs Chrome or Edge — typing works everywhere.');
  if (pttRec) return stopListening();
  Voice.stop();
  Wake.pause();
  const rec = new SR();
  pttRec = rec;
  rec.lang = navigator.language || 'en-GB';
  rec.interimResults = true;
  rec.continuous = false;
  let finalText = '';
  rec.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript;
    }
    $('#q').value = (finalText + interim).trim();
  };
  rec.onerror = (e) => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') toast('Microphone blocked — click the icon in the address bar and allow the mic.');
    else if (e.error === 'no-speech') toast('I heard nothing, sir. Try again a touch louder.');
    else if (e.error !== 'aborted') toast(`Mic error: ${e.error}`);
  };
  rec.onend = () => {
    pttRec = null;
    if (state === 'listening') setState('idle');
    const text = finalText.trim() || $('#q').value.trim();
    if (text && finalText) submit(text); else Wake.resume();
  };
  setState('listening');
  try { rec.start(); } catch { pttRec = null; setState('idle'); }
}
function stopListening() { if (pttRec) pttRec.stop(); }

// Always-on wake word: "Jarvis, what's ..." or "Jarvis" ... (pause) ... "what's ..."
const Wake = {
  enabled: false,
  rec: null,
  armedUntil: 0,
  paused: false,
  start() {
    if (!SR) { toast('The wake word needs Chrome or Edge.'); return false; }
    this.enabled = true;
    this.paused = false;
    this.listen();
    return true;
  },
  stopAll() {
    this.enabled = false;
    if (this.rec) { try { this.rec.abort(); } catch {} this.rec = null; }
  },
  pause() {
    this.paused = true;
    if (this.rec) { try { this.rec.abort(); } catch {} this.rec = null; }
  },
  resume() {
    this.paused = false;
    if (this.enabled && !this.rec && !pttRec && !Voice.speaking) setTimeout(() => this.listen(), 250);
  },
  listen() {
    if (!this.enabled || this.paused || this.rec || pttRec || Voice.speaking) return;
    const rec = new SR();
    this.rec = rec;
    rec.lang = navigator.language || 'en-GB';
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (!e.results[i].isFinal) continue;
        const heard = e.results[i][0].transcript.trim();
        const m = heard.match(/\b(?:jarvis|jervis|travis)\b[\s,.!?]*(.*)$/i);
        if (m) {
          const command = m[1].trim();
          if (command.length > 2) { this.armedUntil = 0; submit(command); }
          else { this.armedUntil = Date.now() + 8000; setState('listening', '● at your service…'); Voice.speak(`Yes, ${status.title}?`); }
        } else if (Date.now() < this.armedUntil && heard.length > 2) {
          this.armedUntil = 0;
          submit(heard);
        }
      }
    };
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        toast('Microphone blocked — wake word switched off.');
        $('#opt-wake').checked = false;
        this.stopAll();
      }
    };
    rec.onend = () => {
      if (this.rec === rec) this.rec = null;
      if (this.enabled && !this.paused) setTimeout(() => this.listen(), 300);
    };
    try { rec.start(); } catch { this.rec = null; }
  },
};

// ------------------------------------------------------------------ asking

async function post(url, body) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

let typing = 0;
async function typeOut(el, text) {
  const id = ++typing;
  el.textContent = '';
  const step = Math.max(1, Math.round(text.length / 90));
  for (let i = 0; i < text.length; i += step) {
    if (id !== typing) return;
    el.textContent = text.slice(0, i + step);
    await sleep(14);
  }
}

function showAnswer(question, text, ids = [], isError = false) {
  const box = $('#answer');
  box.classList.add('show');
  box.classList.toggle('error', isError);
  $('#answer-q').textContent = question ? `You: ${question}` : '';
  typeOut($('#answer-text'), text);
  const chips = ids.map(nodeById).filter(Boolean);
  $('#answer-sources').innerHTML = chips.length
    ? '<span class="lead">Sources</span>' + chips.map((n) => `<button type="button" data-id="${n.id}"><i style="background:${groupColor(n.group)}"></i>${escapeHtml(n.label)}</button>`).join('')
    : '';
}
$('#answer-sources').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-id]');
  if (b) focusNode(Number(b.dataset.id));
});

let busy = false;
async function submit(raw) {
  const text = (raw || '').trim();
  if (!text || busy) return;
  Voice.stop();
  $('#q').value = '';
  if (REMEMBER_RE.test(text)) return remember(text);
  busy = true;
  setState('thinking');
  showAnswer(text, '…');
  try {
    const r = await post('/chat', { message: text, session: SESSION });
    const ids = (r.nodes || []).filter((i) => nodeById(i));
    showAnswer(text, r.answer, ids, !!r.error);
    if (r.brain) setBrain(r.brain, !r.error && !r.brain.startsWith('Offline'));
    refreshQuota();
    if (r.warning) toast(`Free models busy — answered from the index. (${r.warning})`, 7000);
    // Fly-to-source: prove where the answer came from (small talk has no sources -> no camera move).
    if (ids.length >= 4) focusCluster(ids);
    else if (ids.length) focusNode(ids[0], { extra: ids.slice(1) });
    setState('idle');
    busy = false;
    await Voice.speak(r.answer);
  } catch (err) {
    showAnswer(text, `I couldn't reach my own server, ${status.title}. Is server.py still running? (${err.message})`, [], true);
    setState('idle');
  } finally {
    busy = false;
    if (state !== 'speaking' && state !== 'listening') setState('idle');
  }
}

// ------------------------------------------------------------------ total recall: grow the galaxy live

async function remember(text) {
  busy = true;
  setState('filing');
  try {
    const r = await post('/remember', { text });
    const gd = Graph.graphData();
    const node = r.node;
    if (node.id !== gd.nodes.length) {
      toast('The galaxy changed on disk — reloading to stay in sync.');
      return setTimeout(() => location.reload(), 1500);
    }
    const anchor = r.anchor != null ? gd.nodes[r.anchor] : null;
    const jitter = () => (Math.random() - 0.5) * 14;
    node.x = (anchor?.x ?? 0) + jitter();
    node.y = (anchor?.y ?? 0) + jitter();
    node.z = (anchor?.z ?? 0) + jitter();
    node.__born = performance.now();
    groupColor(node.group); // registers a brand-new group (e.g. "captures") for the legend
    r.links.forEach(addEdge);
    Graph.graphData({ nodes: [...gd.nodes, node], links: [...gd.links, ...r.links] });
    updateStats();
    renderLegend();
    showAnswer(text, r.line, [node.id]);
    setTimeout(() => focusNode(node.id), 350);
    setState('idle');
    busy = false;
    await Voice.speak(r.line);
  } catch (err) {
    showAnswer(text, `I'm afraid I couldn't file that: ${err.message}`, [], true);
  } finally {
    busy = false;
    if (state === 'thinking') setState('idle');
  }
}

// ------------------------------------------------------------------ input wiring

$('#ask').addEventListener('submit', (e) => { e.preventDefault(); submit($('#q').value); });
$('#q').addEventListener('input', () => { if (Voice.speaking) Voice.stop(); });
$('#mic').addEventListener('click', () => startListening());

addEventListener('keydown', (e) => {
  const inField = e.target === $('#q');
  if (e.key === 'Escape') {
    Voice.stop();
    if (pttRec) stopListening();
    if (inField) $('#q').blur(); else clearFocus();
    return;
  }
  if (inField || e.ctrlKey || e.metaKey || e.altKey || !engaged) return;
  if (e.key === '/') { e.preventDefault(); $('#q').focus(); }
  else if (e.key === 'm' || e.key === 'M') { e.preventDefault(); startListening(); }
  else if (e.key === 'r' || e.key === 'R') overview();
});

$('#opt-voice').checked = Voice.enabled;
$('#opt-voice').addEventListener('change', (e) => { Voice.enabled = e.target.checked; prefs.set('voice', Voice.enabled); if (!Voice.enabled) Voice.stop(); });
$('#opt-drift').checked = driftEnabled;
$('#opt-drift').addEventListener('change', (e) => {
  driftEnabled = e.target.checked;
  prefs.set('drift', driftEnabled);
  controls.autoRotate = driftEnabled;
});
$('#opt-wake').addEventListener('change', (e) => {
  if (e.target.checked) {
    if (Wake.start()) toast('Wake word on — say “Jarvis, …” any time.'); else e.target.checked = false;
  } else Wake.stopAll();
});

// ------------------------------------------------------------------ animation: reactor level, births, sky

// OpenRouter's 50/day allowance -- shown only while an OpenRouter model is the active brain.
let lastQuota = null;
const quotaSuffix = (label) => (lastQuota && /^OpenRouter/.test(label) ? ` · ${lastQuota.remaining}/${lastQuota.limit} free today` : '');
function showQuota(q) {
  lastQuota = q || null;
  if (status.brain) $('#stat-brain').textContent = `Brain: ${status.brain}${quotaSuffix(status.brain)}`;
}
async function refreshQuota() {
  try { showQuota((await fetch('/api/status').then((r) => r.json())).quota); } catch { /* server busy */ }
}

function setBrain(label, live) {
  status.brain = label;
  $('#stat-brain').textContent = `Brain: ${label}${quotaSuffix(label)}`;
  $('#brain-dot').className = 'brain-dot ' + (live ? 'on' : 'off');
}

let level = 0.2;
function frame(now) {
  const t = now / 1000;
  let target;
  if (state === 'speaking') {
    speechLevel *= 0.94;
    target = 0.35 + 0.65 * Math.max(speechLevel, 0.25 + 0.2 * Math.abs(Math.sin(t * 9.3) * Math.sin(t * 4.1)));
  } else if (state === 'listening') target = 0.55 + 0.15 * Math.sin(t * 5);
  else if (state === 'thinking') target = 0.45 + 0.25 * Math.abs(Math.sin(t * 3.5));
  else target = 0.18 + 0.08 * Math.sin(t * 1.3);
  level += (target - level) * 0.18;
  document.documentElement.style.setProperty('--lvl', level.toFixed(3));

  for (const n of Graph.graphData().nodes) {
    const p = n.__parts;
    if (!p) continue;
    let s = p.glowBase;
    if (n.__born) {
      const k = (now - n.__born) / 2600;
      if (k >= 1) delete n.__born;
      else s *= 1 + 4.5 * (1 - k) * Math.abs(Math.sin(k * Math.PI * 3));
    }
    if (n.id === hl.focus && state === 'speaking') s *= 1 + 0.35 * level;
    p.glow.scale.setScalar(s);
  }
  sky.rotation.y += 0.00004;
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ------------------------------------------------------------------ boot sequence

let engaged = false;
const bootLog = $('#boot-log');
function logLine(html) { const li = document.createElement('li'); li.innerHTML = html; bootLog.appendChild(li); }

async function boot() {
  updateStats();
  tickClock();
  renderLegend();
  logLine(`Indexing knowledge base… <b>${GRAPH.meta.count}</b> notes`);
  await sleep(320);
  logLine(`Mapping synaptic links… <b>${GRAPH.meta.links}</b> connections across <b>${groups.length}</b> constellations`);
  await sleep(320);
  try {
    const s = await fetch('/api/status').then((r) => r.json());
    Object.assign(status, { count: s.count, title: s.user_title || 'sir', brain: s.brain, backend: s.backend });
    setBrain(s.brain, s.backend !== 'offline');
    logLine(`Cognitive core… <b>${escapeHtml(s.brain)}</b>${s.fallbacks ? ` + ${s.fallbacks} fallback models` : ''}`);
    if (s.quota) logLine(`OpenRouter backup… <b>${s.quota.remaining}</b> of ${s.quota.limit} free requests left today`);
    showQuota(s.quota);
  } catch {
    setBrain('server unreachable', false);
    logLine('Cognitive core… <b>server unreachable</b> — run python server.py');
  }
  await sleep(320);
  if (!Voice.voice) Voice.pick();
  logLine(`Voice synthesis… <b>${escapeHtml(Voice.voice ? Voice.voice.name : 'browser default')}</b>${SR ? '' : ' · mic needs Chrome/Edge'}`);
  const btn = $('#engage');
  btn.disabled = false;
  btn.textContent = 'Engage';
  btn.focus();
}

function greetingLine() {
  const h = new Date().getHours();
  const part = h < 5 ? 'Good evening' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  let line = `${part}, ${status.title}. ${Graph.graphData().nodes.length} notes indexed, all present and accounted for.`;
  if (status.backend === 'offline') line += ' I am running without an API key, so expect a diligent librarian rather than a genius.';
  return line;
}

async function engage() {
  if (engaged || $('#engage').disabled) return;
  engaged = true;
  $('#boot').classList.add('gone');
  // Cinematic entrance: start far out, then sweep in.
  Graph.cameraPosition({ x: 0, y: 260, z: 1400 }, { x: 0, y: 0, z: 0 }, 0);
  await sleep(50);
  Graph.zoomToFit(2600, 60);
  setTimeout(() => { if (driftEnabled) controls.autoRotate = true; }, 2700);
  const line = greetingLine();
  showAnswer('', line);
  await Voice.speak(line); // this click is the user gesture that unlocks audio
}
$('#boot').addEventListener('click', engage);
$('#engage').addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); engage(); } });

boot();
