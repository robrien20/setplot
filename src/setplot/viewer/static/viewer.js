// Set view — fetches per-step JSONs from /api/sets/{id}/* and renders them on
// the canvas stack inherited from Phase 1. Audio playback uses wavesurfer.js
// fed pre-computed peaks, so a 7h set draws instantly without decoding the
// whole file in the browser.
//
// All canvas-rendering functions (drawMinimap, drawBPM, drawCoverage, drawKey)
// are unchanged from Phase 1 — only the data plumbing and the scrub bar were
// adapted.

import WaveSurfer from "./vendor/wavesurfer.esm.js";

// ============================================================================
// Set id from URL
// ============================================================================
const SET_ID = new URLSearchParams(window.location.search).get("id");
if (!SET_ID) {
  document.body.innerHTML = `<p style="color:#c44a4a;padding:24px;font-family:monospace;">missing ?id=&lt;set_id&gt; — return to <a href="/" style="color:#4dd0e1;">library</a></p>`;
  throw new Error("no set_id");
}

// ============================================================================
// Data & state
// ============================================================================
let DATA = {
  duration_s: 0,
  bpm: [],
  keys: [],
  merged: [],
  windows: {},
  stride: 10,
  title: "",
};
let viewStart = 0;
let viewEnd = 0;
let dragMode = null;
let minimapDragAnchor = null;
let followPlayhead = true;
let wavesurfer = null;
let SERVICES = { spotify: { enabled: false }, apple: { enabled: false } };

const audio         = document.getElementById("audio");
const minimapCv     = document.getElementById("minimap-canvas");
const bpmCv         = document.getElementById("bpm-canvas");
const covCv         = document.getElementById("coverage-canvas");
const keyCv         = document.getElementById("key-canvas");
const bpmTooltip    = document.getElementById("bpm-tooltip");
const timeDisplay   = document.getElementById("time-display");
const bpmNow        = document.getElementById("bpm-now");
const keyNow        = document.getElementById("key-now");
const nowCursor     = document.getElementById("now-cursor");
const nowContent    = document.getElementById("now-content");
const tracklistBody = document.getElementById("tracklist-body");
const searchInput   = document.getElementById("search");
const trackCount    = document.getElementById("track-count");
const onlyVisibleCb = document.getElementById("only-visible");
const zoomInd       = document.getElementById("zoom-indicator");
const btnPlay       = document.getElementById("btn-play");
const headerTitle   = document.getElementById("header-title");
const headerMeta    = document.getElementById("header-meta");
const statusPill    = document.getElementById("status-pill");
const waveformEmpty = document.getElementById("waveform-empty");

const GENRE_BANDS = [
  [60,  90,  "#3a4a66", "hip-hop / downtempo"],
  [90,  110, "#3e506c", "house slow / dub"],
  [110, 128, "#425672", "house / tech-house"],
  [128, 140, "#465c78", "techno / trance"],
  [140, 160, "#4a627e", "hard techno / hardgroove"],
  [160, 180, "#4e6884", "drum & bass / jungle"],
  [180, 220, "#526e8a", "hardcore / gabber"],
];

function fmt(sec) {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t/3600), m = Math.floor((t%3600)/60), s = t%60;
  return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}
function fmtShort(sec) {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t/3600), m = Math.floor((t%3600)/60), s = t%60;
  if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  return `${m}:${String(s).padStart(2,"0")}`;
}

function bpmAt(t) {
  if (!DATA || !DATA.bpm.length) return null;
  let lo = 0, hi = DATA.bpm.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (DATA.bpm[mid][0] <= t) lo = mid; else hi = mid - 1;
  }
  return DATA.bpm[lo][1];
}
function keyAt(t) {
  if (!DATA || !DATA.keys || !DATA.keys.length) return null;
  let lo = 0, hi = DATA.keys.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (DATA.keys[mid][0] <= t) lo = mid; else hi = mid - 1;
  }
  const [, camelot, name, corr, margin] = DATA.keys[lo];
  return { camelot, name, corr, margin };
}
function nearestWindow(t) { return Math.floor(t / DATA.stride) * DATA.stride; }

function camelotColor(camelot, confidence = 1.0) {
  if (!camelot) return "#333";
  const hour = parseInt(camelot);
  const ring = camelot.slice(-1);
  const hue = (hour - 1) / 12;
  const sat = 0.75 * Math.max(0.2, Math.min(1, confidence));
  const val = ring === "A" ? 0.55 : 0.85;
  return hsvToRgb(hue, sat, val);
}
function hsvToRgb(h, s, v) {
  let r, g, b;
  const i = Math.floor(h * 6);
  const f = h * 6 - i;
  const p = v * (1 - s);
  const q = v * (1 - f * s);
  const t = v * (1 - (1 - f) * s);
  switch (i % 6) {
    case 0: r = v; g = t; b = p; break;
    case 1: r = q; g = v; b = p; break;
    case 2: r = p; g = v; b = t; break;
    case 3: r = p; g = q; b = v; break;
    case 4: r = t; g = p; b = v; break;
    case 5: r = v; g = p; b = q; break;
  }
  return `rgb(${Math.round(r*255)},${Math.round(g*255)},${Math.round(b*255)})`;
}

// ============================================================================
// Zoom
// ============================================================================
function clampView() {
  const dur = DATA.duration_s;
  const minSpan = 30;
  if (viewEnd - viewStart < minSpan) viewEnd = viewStart + minSpan;
  if (viewStart < 0) { viewEnd -= viewStart; viewStart = 0; }
  if (viewEnd > dur) { viewStart -= (viewEnd - dur); viewEnd = dur; }
  if (viewStart < 0) viewStart = 0;
}
function setZoom(start, end) {
  viewStart = start; viewEnd = end;
  clampView();
  const span = viewEnd - viewStart;
  zoomInd.textContent = `view: ${fmtShort(viewStart)} → ${fmtShort(viewEnd)}  (${fmtShort(span)})`;
  redrawAll();
  if (DATA) renderTracklist();
}
function zoomAroundTime(tCenter, factor) {
  if (followPlayhead) tCenter = audio.currentTime;
  const span = (viewEnd - viewStart) * factor;
  const frac = followPlayhead ? 0.5 : (tCenter - viewStart) / (viewEnd - viewStart);
  const newStart = tCenter - span * frac;
  setZoom(newStart, newStart + span);
}
function autoFollow() {
  if (!followPlayhead) return;
  const span = viewEnd - viewStart;
  if (span >= DATA.duration_s - 1) return;
  const newStart = audio.currentTime - span / 2;
  viewStart = newStart;
  viewEnd = newStart + span;
  clampView();
}
function setFollow(v) {
  followPlayhead = v;
  const btn = document.getElementById("btn-follow");
  if (btn) {
    btn.textContent = v ? "⤓ follow" : "⬒ free";
    btn.style.background = v ? "rgba(77,208,225,0.25)" : "";
    btn.style.borderColor = v ? "var(--accent)" : "";
  }
}

// ============================================================================
// Canvas helpers
// ============================================================================
function rescale(c) {
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}
function timeFromCanvasX(canvas, clientX, start, end) {
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
  return start + (x / rect.width) * (end - start);
}

// ============================================================================
// Drawing — these are unchanged from Phase 1, just reading DATA.* fed by API.
// ============================================================================
function drawMinimap() {
  const { ctx, w, h } = rescale(minimapCv);
  const dur = DATA.duration_s;
  if (dur <= 0) return;
  const px = t => (t / dur) * w;

  for (const [lo, hi, color] of GENRE_BANDS) {
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.25;
    const yLo = h - ((lo - 60) / 160) * h;
    const yHi = h - ((hi - 60) / 160) * h;
    ctx.fillRect(0, yHi, w, yLo - yHi);
  }
  ctx.globalAlpha = 1;

  const runs = findNoMatchRuns(60);
  ctx.fillStyle = "rgba(196,74,74,0.2)";
  for (const [s, e] of runs) ctx.fillRect(px(s), 0, px(e)-px(s), h);

  ctx.strokeStyle = "#e6ecf5";
  ctx.lineWidth = 0.6;
  ctx.beginPath();
  for (let i = 0; i < DATA.bpm.length; i++) {
    const [t, b] = DATA.bpm[i];
    const y = h - ((Math.min(220, Math.max(60, b)) - 60) / 160) * h;
    if (i === 0) ctx.moveTo(px(t), y); else ctx.lineTo(px(t), y);
  }
  ctx.stroke();

  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.fillStyle = "rgba(230,236,245,0.4)";
  ctx.font = "9px monospace";
  for (let hr = 1; hr * 3600 < dur; hr++) {
    const x = px(hr * 3600);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.fillText(`${hr}h`, x + 2, 10);
  }

  const vx1 = px(viewStart), vx2 = px(viewEnd);
  ctx.fillStyle = "rgba(77,208,225,0.15)";
  ctx.fillRect(vx1, 0, vx2-vx1, h);
  ctx.strokeStyle = "rgba(77,208,225,0.9)";
  ctx.lineWidth = 2;
  ctx.strokeRect(vx1, 0.5, vx2-vx1, h-1);
  ctx.fillStyle = "rgba(77,208,225,1)";
  ctx.fillRect(vx1 - 2, h/2 - 6, 4, 12);
  ctx.fillRect(vx2 - 2, h/2 - 6, 4, 12);

  const phX = px(audio.currentTime || 0);
  ctx.strokeStyle = "#ffca28";
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(phX, 0); ctx.lineTo(phX, h); ctx.stroke();
}

// Visible-range autoscale, snapped to "nice" round numbers. Uses p2/p98
// percentiles instead of strict min/max so a stray octave-error spike
// (140 → 70 → 140) can't drag the axis low and squash the real data.
//
// Headroom rule: the modal BPM (median) sits no higher than 2/3 up the
// canvas — i.e. always ≥ 1/3 from the top. So if a 142-BPM set has a
// real 3-min dip to 95, the dip is visible at the bottom but the dense
// 140-142 cluster keeps breathing room above instead of pinning the top.
function bpmYRange(vs, ve) {
  const vals = [];
  for (const [t, b] of DATA.bpm) {
    if (t < vs - 5 || t > ve + 5) continue;
    if (b > 30 && b < 240) vals.push(b);
  }
  if (vals.length < 5) return [60, 220];
  vals.sort((a, b) => a - b);
  const lo = vals[Math.floor(vals.length * 0.02)];
  const hi = vals[Math.min(vals.length - 1, Math.floor(vals.length * 0.98))];
  const mode = vals[vals.length >> 1];
  const pad = Math.max((hi - lo) * 0.15, 5);
  let yMin = Math.floor((lo - pad) / 5) * 5;
  let yMax = Math.ceil((hi + pad) / 5) * 5;
  // y_mode ≥ h/3  ⇔  yMax ≥ mode + (mode - yMin) / 2.
  const minYMaxForHeadroom = mode + (mode - yMin) / 2;
  if (yMax < minYMaxForHeadroom) yMax = Math.ceil(minYMaxForHeadroom / 5) * 5;
  if (yMax - yMin < 15) yMax = yMin + 15;
  return [Math.max(40, yMin), Math.min(240, yMax)];
}

// Gridline interval scales with span so we get ~5–10 lines regardless of zoom.
function bpmGridlines(yMin, yMax) {
  const span = yMax - yMin;
  const step = span <= 25 ? 2 : span <= 60 ? 5 : span <= 120 ? 10 : 20;
  const lines = [];
  for (let b = Math.ceil(yMin / step) * step; b <= yMax; b += step) lines.push(b);
  return lines;
}

function drawBPM() {
  const { ctx, w, h } = rescale(bpmCv);
  const vs = viewStart, ve = viewEnd;
  const span = ve - vs;
  if (span <= 0) return;
  const [yMin, yMax] = bpmYRange(vs, ve);
  const px = t => ((t - vs) / span) * w;
  const py = b => h - ((Math.min(yMax, Math.max(yMin, b)) - yMin) / (yMax - yMin)) * h;

  for (const [lo, hi, color] of GENRE_BANDS) {
    ctx.fillStyle = color; ctx.globalAlpha = 0.3;
    ctx.fillRect(0, py(hi), w, py(lo) - py(hi));
  }
  ctx.globalAlpha = 1;

  const runs = findNoMatchRuns(60);
  ctx.fillStyle = "rgba(196,74,74,0.22)";
  for (const [s, e] of runs) {
    if (e < vs || s > ve) continue;
    const x1 = Math.max(0, px(s)), x2 = Math.min(w, px(e));
    if (x2 > x1) ctx.fillRect(x1, 0, x2-x1, h);
  }

  ctx.strokeStyle = "rgba(170,200,255,0.35)";
  ctx.lineWidth = 0.7;
  ctx.beginPath();
  let first = true;
  for (let i = 0; i < DATA.bpm.length; i++) {
    const [t, b] = DATA.bpm[i];
    if (t < vs - 20 || t > ve + 20) continue;
    if (first) { ctx.moveTo(px(t), py(b)); first = false; }
    else ctx.lineTo(px(t), py(b));
  }
  ctx.stroke();

  if (!drawBPM._segments) drawBPM._segments = computeTrackSegments();
  ctx.lineWidth = 3;
  for (const seg of drawBPM._segments) {
    if (seg.end < vs || seg.start > ve) continue;
    const x1 = Math.max(0, px(seg.start));
    const x2 = Math.min(w, px(seg.end));
    const y = py(seg.bpm);
    ctx.strokeStyle = seg.score >= 80 ? "rgba(60,179,113,0.9)"
                    : seg.score >= 40 ? "rgba(255,202,40,0.9)"
                    : "rgba(255,140,66,0.9)";
    ctx.beginPath();
    ctx.moveTo(x1, y);
    ctx.lineTo(x2, y);
    ctx.stroke();
  }

  const k = 5;
  ctx.strokeStyle = "#e6ecf5";
  ctx.lineWidth = 1.3;
  ctx.beginPath();
  first = true;
  for (let i = 0; i < DATA.bpm.length; i++) {
    const t = DATA.bpm[i][0];
    if (t < vs - 20 || t > ve + 20) continue;
    const slice = [];
    for (let j = Math.max(0, i-k); j < Math.min(DATA.bpm.length, i+k+1); j++) slice.push(DATA.bpm[j][1]);
    slice.sort((a,b)=>a-b);
    const med = slice[slice.length >> 1];
    if (first) { ctx.moveTo(px(t), py(med)); first = false; }
    else ctx.lineTo(px(t), py(med));
  }
  ctx.stroke();

  // Genre labels — only show bands that overlap the visible y-range, so a
  // narrow zoom around 130 BPM doesn't spill labels for hardcore/d&b off-axis.
  ctx.fillStyle = "#8798b6";
  ctx.font = "9px -apple-system, sans-serif";
  ctx.textAlign = "left";
  for (const [lo, hi, , label] of GENRE_BANDS) {
    if (hi <= yMin || lo >= yMax) continue;
    const visLo = Math.max(lo, yMin), visHi = Math.min(hi, yMax);
    if ((visHi - visLo) / (hi - lo) < 0.25) continue;  // mostly off-screen
    ctx.fillText(label, w + 4, (py(visLo) + py(visHi)) / 2 + 3);
  }

  // BPM grid + labels — interval scales with the visible span. Major lines
  // (every 4th gridline-step or multiples of 20) are slightly brighter.
  ctx.font = "10px monospace";
  ctx.textAlign = "left";
  const gridSteps = bpmGridlines(yMin, yMax);
  const majorEvery = gridSteps.length > 8 ? 4 : 2;
  gridSteps.forEach((b, i) => {
    const yy = py(b);
    const isMajor = (i % majorEvery === 0) || (b % 20 === 0);
    ctx.strokeStyle = isMajor ? "rgba(255,255,255,0.18)" : "rgba(255,255,255,0.08)";
    ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(w, yy); ctx.stroke();
    const lbl = String(b);
    const tw = ctx.measureText(lbl).width;
    ctx.fillStyle = "rgba(14,20,32,0.7)";
    ctx.fillRect(0, yy - 6, tw + 6, 12);
    ctx.fillStyle = "rgba(230,236,245,0.95)";
    ctx.fillText(lbl, 3, yy + 3);
  });

  const tickIntervals = [5, 10, 30, 60, 300, 600, 1800, 3600];
  const tickPx = 80;
  let tickInterval = tickIntervals[0];
  for (const ti of tickIntervals) {
    if ((ti / span) * w >= tickPx) { tickInterval = ti; break; }
    tickInterval = ti;
  }
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.fillStyle = "rgba(230,236,245,0.55)";
  ctx.font = "10px monospace";
  ctx.textAlign = "left";
  const firstTick = Math.ceil(vs / tickInterval) * tickInterval;
  for (let tt = firstTick; tt <= ve; tt += tickInterval) {
    const x = px(tt);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.fillText(fmtShort(tt), x + 3, 11);
  }

  ctx.lineWidth = 0.6;
  for (const tk of DATA.merged) {
    if (tk.start < vs || tk.start > ve) continue;
    const x = px(tk.start);
    ctx.strokeStyle = "rgba(255,202,40,0.5)";
    ctx.beginPath(); ctx.moveTo(x, h * 0.05); ctx.lineTo(x, h); ctx.stroke();
  }

  const labelPx = 140;
  const labeled = [];
  const sorted = [...DATA.merged].filter(tk => tk.start >= vs && tk.start <= ve)
                    .sort((a,b) => b.hits - a.hits);
  ctx.font = "10px -apple-system, sans-serif";
  ctx.fillStyle = "rgba(230,236,245,0.95)";
  ctx.textAlign = "left";
  for (const tk of sorted) {
    const x = px(tk.start);
    if (labeled.some(lx => Math.abs(lx - x) < labelPx)) continue;
    const label = `${tk.artists.slice(0,20)} — ${tk.title.slice(0,26)}`;
    const y = 28;
    ctx.fillStyle = "rgba(26,34,51,0.85)";
    const textW = ctx.measureText(label).width + 8;
    ctx.fillRect(x + 3, y - 10, textW, 14);
    ctx.strokeStyle = "rgba(255,202,40,0.45)";
    ctx.lineWidth = 0.6;
    ctx.strokeRect(x + 3, y - 10, textW, 14);
    ctx.fillStyle = "rgba(230,236,245,0.95)";
    ctx.fillText(label, x + 7, y);
    labeled.push(x);
    if (labeled.length > 30) break;
  }

  const t = audio.currentTime || 0;
  if (t >= vs && t <= ve) {
    const phX = px(t);
    ctx.strokeStyle = "#ffca28";
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(phX, 0); ctx.lineTo(phX, h); ctx.stroke();
    ctx.fillStyle = "#ffca28";
    ctx.beginPath(); ctx.arc(phX, 6, 3.5, 0, Math.PI*2); ctx.fill();
  }
}

function drawCoverage() {
  const { ctx, w, h } = rescale(covCv);
  const vs = viewStart, ve = viewEnd;
  const span = ve - vs;
  if (span <= 0) return;
  const stride = DATA.stride;
  const firstW = Math.floor(vs / stride) * stride;
  const lastW = Math.ceil(ve / stride) * stride;
  for (let ws = firstW; ws < lastW; ws += stride) {
    const cands = DATA.windows[ws];
    let color;
    if (!cands || cands.length === 0) color = "#333c52";
    else {
      const top = cands[0].score, n = cands.length;
      if (top >= 80 && n === 1) color = "#3cb371";
      else if (top >= 80) color = "#6fc67f";
      else if (top >= 40) color = "#ffca28";
      else color = "#ff8c42";
    }
    ctx.fillStyle = color;
    const x1 = ((ws - vs) / span) * w;
    const x2 = ((ws + stride - vs) / span) * w;
    ctx.fillRect(x1, 0, Math.max(1, x2 - x1 + 1), h);
  }
  const t = audio.currentTime || 0;
  if (t >= vs && t <= ve) {
    const phX = ((t - vs) / span) * w;
    ctx.fillStyle = "#ffca28"; ctx.fillRect(phX - 1, 0, 2, h);
  }
}

function computeTrackSegments() {
  const stride = DATA.stride;
  const segs = [];
  for (const tk of DATA.merged) {
    const s = tk.first !== undefined ? tk.first : tk.start;
    const e = (tk.last !== undefined ? tk.last : tk.start) + stride;
    const vals = [];
    for (const [ti, b] of DATA.bpm) {
      if (ti >= s && ti <= e) vals.push(b);
      if (ti > e) break;
    }
    if (vals.length === 0) continue;
    vals.sort((a,b)=>a-b);
    const bpm = vals[vals.length >> 1];
    segs.push({ start: s, end: e, bpm, score: tk.score, title: tk.title, artists: tk.artists });
  }
  return segs;
}

function findNoMatchRuns(minDuration) {
  const runs = [];
  const stride = DATA.stride;
  if (DATA.duration_s <= 0) return runs;
  const nWindows = Math.ceil(DATA.duration_s / stride);
  let curStart = null;
  for (let i = 0; i < nWindows; i++) {
    const w = i * stride;
    const hit = DATA.windows[w] && DATA.windows[w].length > 0;
    if (!hit) { if (curStart === null) curStart = w; }
    else {
      if (curStart !== null && w - curStart >= minDuration) runs.push([curStart, w]);
      curStart = null;
    }
  }
  if (curStart !== null) {
    const end = nWindows * stride;
    if (end - curStart >= minDuration) runs.push([curStart, end]);
  }
  return runs;
}

function redrawAll() {
  autoFollow();
  drawMinimap();
  drawBPM();
  drawCoverage();
  drawKey();
}

// Plot Camelot key as a dot scatter on a 12-lane grid (hour 1-12 on the y-axis).
// 12A and 12B share the same y-coordinate; the dot colour distinguishes the ring
// (A=minor → cyan, B=major → yellow). Windows below KEY_CONF_THRESHOLD are
// suppressed to keep low-confidence flicker from confusing the eye.
const KEY_CONF_THRESHOLD = 0.45;
const KEY_COLOR_A = "rgba(77, 208, 225, 0.85)";   // minor — cyan
const KEY_COLOR_B = "rgba(255, 202, 40, 0.85)";   // major — yellow

function drawKey() {
  const { ctx, w, h } = rescale(keyCv);
  const vs = viewStart, ve = viewEnd;
  const span = ve - vs;
  if (span <= 0) return;

  // Grid + axis labels (drawn even when DATA.keys is empty so the panel doesn't go blank).
  const TOP_PAD = 8;
  const BOT_PAD = 6;
  const usableH = h - TOP_PAD - BOT_PAD;
  const laneH = usableH / 12;
  // 12 at top, 1 at bottom — bigger numbers higher matches "moving up" musically.
  const yForHour = (hour) => TOP_PAD + (12 - hour + 0.5) * laneH;

  ctx.font = "9px monospace";
  ctx.textAlign = "left";
  for (let hour = 1; hour <= 12; hour++) {
    const yy = yForHour(hour);
    ctx.strokeStyle = hour % 3 === 0 ? "rgba(255,255,255,0.14)" : "rgba(255,255,255,0.06)";
    ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(w, yy); ctx.stroke();
    const lbl = String(hour);
    const tw = ctx.measureText(lbl).width;
    ctx.fillStyle = "rgba(14,20,32,0.7)";
    ctx.fillRect(0, yy - 5, tw + 6, 10);
    ctx.fillStyle = "rgba(230,236,245,0.85)";
    ctx.fillText(lbl, 3, yy + 3);
  }

  if (!DATA.keys || !DATA.keys.length) return;

  // Plot dots; size scales with confidence so strong reads stand out.
  for (const [t, camelot, , corr] of DATA.keys) {
    if (corr < KEY_CONF_THRESHOLD) continue;
    if (t < vs || t > ve) continue;
    const hour = parseInt(camelot, 10);
    if (!hour) continue;
    const ring = camelot.slice(-1);
    const x = ((t - vs) / span) * w;
    const y = yForHour(hour);
    const radius = 1.4 + Math.min(1, (corr - KEY_CONF_THRESHOLD) / 0.4) * 2.2;
    ctx.fillStyle = ring === "B" ? KEY_COLOR_B : KEY_COLOR_A;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  // Highlight current playhead's hour with a faint horizontal accent + vertical line.
  const tCur = audio.currentTime || 0;
  if (tCur >= vs && tCur <= ve) {
    const here = keyAt(tCur);
    if (here && here.corr >= KEY_CONF_THRESHOLD) {
      const hour = parseInt(here.camelot, 10);
      if (hour) {
        const yy = yForHour(hour);
        ctx.fillStyle = "rgba(255, 202, 40, 0.10)";
        ctx.fillRect(0, yy - laneH / 2, w, laneH);
      }
    }
    const phX = ((tCur - vs) / span) * w;
    ctx.fillStyle = "#ffca28";
    ctx.fillRect(phX - 1, 0, 2, h);
  }
}

// ============================================================================
// Now-playing panel
// ============================================================================
function lookupLinksFor(t, bpm) {
  const bpmLabel = bpm ? ` "${Math.round(bpm)} bpm"` : "";
  const safeTitle = DATA.title || "set";
  const titleQ = encodeURIComponent(safeTitle);
  const q1001 = encodeURIComponent(`${safeTitle} ${fmt(t).slice(0,5)}`);
  const qGoogle = encodeURIComponent(`"${safeTitle}" tracklist${bpmLabel}`);
  const qReddit = encodeURIComponent(`${safeTitle} track ID`);
  return `
    <div style="margin-top:10px; padding-top:8px; border-top:1px solid var(--border); font-size:11px;">
      <div style="color:var(--fg-dim); margin-bottom:5px;">search this moment on human-curated DBs:</div>
      <div style="display:flex; flex-wrap:wrap; gap:5px;">
        <a href="https://www.1001tracklists.com/search/result.php?main_search=${q1001}" target="_blank">1001tracklists</a>
        <a href="https://tracksniff.com/search?q=${titleQ}" target="_blank">TrackSniff</a>
        <a href="https://www.mixesdb.com/db/index.php?title=Special:Search&search=${titleQ}" target="_blank">MixesDB</a>
        <a href="https://www.reddit.com/r/TrackID/search/?q=${qReddit}" target="_blank">r/TrackID</a>
        <a href="https://www.google.com/search?q=${qGoogle}" target="_blank">Google</a>
        <a href="https://www.youtube.com/results?search_query=${titleQ}" target="_blank">YouTube</a>
      </div>
      <div style="color:var(--fg-dim); margin-top:6px; font-size:10px;">
        tip: extract this 30s clip with <code>ffmpeg -ss ${Math.floor(t)} -i source.mp3 -t 30 clip.m4a</code>.
      </div>
    </div>
  `;
}

function renderNow() {
  const t = audio.currentTime;
  const w = nearestWindow(t);
  const cands = DATA.windows[w] || [];
  const bpm = bpmAt(t);
  const key = keyAt(t);
  nowCursor.innerHTML = `window <b>${fmt(w)} → ${fmt(w + DATA.stride)}</b>  ·  ${cands.length} candidate${cands.length===1?"":"s"}` +
    (bpm ? `  ·  BPM ${bpm.toFixed(1)}` : "") +
    (key ? `  ·  <span style="background:${camelotColor(key.camelot, Math.max(0.3, key.corr*1.5))}; color:#0e1420; padding:1px 6px; border-radius:3px; font-weight:600;">${key.camelot}</span> ${key.name}` : "");

  if (cands.length === 0) {
    nowContent.innerHTML = `<div class="no-hit">NO ACR MATCH at this window.<br>Likely an unreleased edit, heavy transition, or track not in the ACR catalog.</div>`
      + lookupLinksFor(t, bpm);
    return;
  }
  let html = "";
  if (cands[0].score < 40) html += `<div class="weak-only">⚠ All candidates are low-confidence (&lt;40). Treat as leads.</div>`;
  for (const c of cands) {
    const cls = c.score >= 80 ? "top" : (c.score >= 40 ? "medium" : "weak");
    const poMm = Math.floor(c.play_offset_ms / 60000);
    const poSs = Math.floor((c.play_offset_ms % 60000) / 1000);
    const durMm = Math.floor(c.duration_ms / 60000);
    const durSs = Math.floor((c.duration_ms % 60000) / 1000);
    const trackPos = c.duration_ms ? `${String(poMm).padStart(2,"0")}:${String(poSs).padStart(2,"0")} / ${String(durMm).padStart(2,"0")}:${String(durSs).padStart(2,"0")}` : "";
    const engine = {1:"fingerprint",2:"humming/cover",3:"fp+reranked"}[c.result_from] || "";
    const links = [];
    const previews = [];
    if (c.apple) previews.push(`<button class="preview-btn" data-preview-service="apple" data-preview-id="${c.apple}" title="play 30s preview from Apple Music">▶ AM</button>`);
    if (c.spotify) previews.push(`<button class="preview-btn" data-preview-service="spotify" data-preview-id="${c.spotify}" title="play 30s preview via Spotify embed">▶ SP</button>`);
    if (c.spotify) links.push(`<a href="https://open.spotify.com/track/${c.spotify}" target="_blank">Spotify</a>`);
    if (c.apple) links.push(`<a href="https://music.apple.com/song/${c.apple}" target="_blank">Apple Music</a>`);
    if (c.deezer) links.push(`<a href="https://www.deezer.com/track/${c.deezer}" target="_blank">Deezer</a>`);
    if (c.youtube) links.push(`<a href="https://youtu.be/${c.youtube}" target="_blank">YouTube</a>`);
    if (c.isrc) links.push(`<a href="https://musicbrainz.org/search?type=recording&query=isrc:${c.isrc}" target="_blank">MB</a>`);
    html += `<div class="candidate ${cls}">
      <div class="candidate-title">${esc(c.title)}</div>
      <div class="candidate-artist">${esc(c.artists)}</div>
      <div class="candidate-meta">score ${c.score}${engine?" · "+engine:""}${trackPos?" · track@"+trackPos:""}${c.album?" · "+esc(c.album):""}${c.label?" · "+esc(c.label):""}${c.isrc?" · ISRC "+c.isrc:""}</div>
      ${previews.length ? `<div class="candidate-previews">${previews.join("")}</div>` : ""}
      ${links.length ? `<div class="candidate-links">${links.join("")}</div>` : ""}
    </div>`;
  }
  html += lookupLinksFor(t, bpm);
  nowContent.innerHTML = html;
  wirePreviewButtons(nowContent);
}
function esc(s) { return (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"})[c]); }

// ============================================================================
// 30s previews (no auth) — Apple via iTunes lookup, Spotify via embed iframe.
// One shared player; clicking another preview tears the previous one down.
// ============================================================================
let previewState = null;  // { service, id, button, audio?, iframe? }

function teardownPreview() {
  if (!previewState) return;
  if (previewState.audio) { try { previewState.audio.pause(); previewState.audio.remove(); } catch (_) {} }
  if (previewState.iframe) { try { previewState.iframe.remove(); } catch (_) {} }
  if (previewState.button) {
    const svc = previewState.service === "apple" ? "AM" : "SP";
    previewState.button.textContent = `▶ ${svc}`;
    previewState.button.classList.remove("playing");
  }
  previewState = null;
}

async function playPreview(service, id, button) {
  if (previewState && previewState.id === id && previewState.service === service) {
    teardownPreview();
    return;
  }
  teardownPreview();
  // Pause the main set audio so the preview is the only thing playing.
  if (!audio.paused) audio.pause();

  if (service === "apple") {
    let info;
    try {
      const r = await fetch(`/api/preview?service=apple&id=${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(`preview lookup ${r.status}`);
      info = await r.json();
    } catch (e) {
      button.textContent = "✗ no preview";
      setTimeout(() => { button.textContent = "▶ AM"; }, 1800);
      return;
    }
    const a = new Audio(info.preview_url);
    a.addEventListener("ended", teardownPreview);
    button.textContent = "⏸ AM";
    button.classList.add("playing");
    previewState = { service, id, button, audio: a };
    a.play().catch(() => teardownPreview());
  } else if (service === "spotify") {
    // Spotify's official embed plays a 30s preview without auth. We drop the
    // iframe inline directly under the button row and let it autoplay.
    const wrapper = button.closest(".candidate") || button.parentElement;
    const iframe = document.createElement("iframe");
    iframe.src = `https://open.spotify.com/embed/track/${encodeURIComponent(id)}?utm_source=setplot`;
    iframe.width = "100%";
    iframe.height = "80";
    iframe.frameBorder = "0";
    iframe.allow = "encrypted-media";
    iframe.style.cssText = "margin-top:6px;border-radius:4px;";
    wrapper.appendChild(iframe);
    button.textContent = "✕ SP";
    button.classList.add("playing");
    previewState = { service, id, button, iframe };
  }
}

function wirePreviewButtons(container) {
  container.querySelectorAll(".preview-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      playPreview(btn.dataset.previewService, btn.dataset.previewId, btn);
    });
  });
}

// Pause any preview when the main set audio starts playing — the user clearly
// wants to hear the set, not a 30s clip.
audio.addEventListener("play", teardownPreview);

// ============================================================================
// Export buttons (visible only for services configured server-side)
// ============================================================================
function renderExportButtons() {
  // Slot a small bar under the header with "Export → Spotify / Apple Music".
  // Hidden if neither service is configured.
  let bar = document.getElementById("export-bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "export-bar";
    bar.style.cssText = "padding:6px 10px;display:flex;gap:8px;align-items:center;";
    const headerEl = document.getElementById("header");
    headerEl.parentElement.insertBefore(bar, headerEl.nextSibling);
  }
  const buttons = [];
  if (SERVICES.spotify && SERVICES.spotify.enabled) {
    buttons.push(`<button class="btn" id="btn-export-spotify">Export → Spotify</button>`);
  }
  if (SERVICES.apple && SERVICES.apple.enabled) {
    buttons.push(`<button class="btn" id="btn-export-apple">Export → Apple Music</button>`);
  }
  if (buttons.length === 0) {
    bar.style.display = "none";
    bar.innerHTML = "";
    return;
  }
  bar.style.display = "flex";
  bar.innerHTML = `<span style="color:var(--fg-dim);font-size:11px;">recognized tracks →</span>${buttons.join("")}<span id="export-status" style="color:var(--fg-dim);font-size:11px;"></span>`;
  const sp = document.getElementById("btn-export-spotify");
  if (sp) sp.addEventListener("click", () => exportSpotify());
  const am = document.getElementById("btn-export-apple");
  if (am) am.addEventListener("click", () => exportApple());
}

async function exportSpotify() {
  const status = document.getElementById("export-status");
  // Probe connection state — if not connected, kick off the OAuth dance.
  let st;
  try {
    st = await fetch("/api/auth/status").then(r => r.json());
  } catch (e) {
    status.textContent = "auth probe failed";
    return;
  }
  if (!st.spotify || !st.spotify.connected) {
    status.textContent = "opening Spotify login…";
    // Pop a window so we don't lose the set page; we'll re-poll status on focus.
    const popup = window.open("/auth/spotify/login", "setplot_spotify_auth", "width=520,height=720");
    const onFocus = async () => {
      window.removeEventListener("focus", onFocus);
      // Brief delay to let the callback finish writing tokens.
      await new Promise(r => setTimeout(r, 600));
      const s2 = await fetch("/api/auth/status").then(r => r.json()).catch(() => null);
      if (s2 && s2.spotify && s2.spotify.connected) {
        status.textContent = "connected. click Export again.";
      } else {
        status.textContent = "not connected — try again.";
      }
    };
    window.addEventListener("focus", onFocus);
    return;
  }

  status.textContent = "exporting → Spotify…";
  try {
    const r = await fetch("/api/export/spotify", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ set_id: SET_ID, public: false }),
    });
    if (!r.ok) {
      const err = await r.text();
      status.textContent = `export failed: ${r.status} ${err.slice(0, 120)}`;
      return;
    }
    const body = await r.json();
    const link = body.playlist_url
      ? `<a href="${body.playlist_url}" target="_blank" style="color:var(--accent);">open playlist ↗</a>`
      : "(no link)";
    const um = body.unmatched && body.unmatched.length
      ? ` · ${body.unmatched.length} unmatched`
      : "";
    status.innerHTML = `✓ ${body.matched} tracks · ${link}${um}`;
  } catch (e) {
    status.textContent = `export failed: ${e}`;
  }
}
async function exportApple() {
  const status = document.getElementById("export-status");
  status.textContent = "loading MusicKit…";
  let mod;
  try {
    mod = await import("./apple-export.js");
  } catch (e) {
    status.textContent = `couldn't load apple-export.js: ${e}`;
    return;
  }
  try {
    const r = await mod.exportToAppleMusic(SET_ID);
    const link = r.playlist_url
      ? `<a href="${r.playlist_url}" target="_blank" style="color:var(--accent);">open playlist ↗</a>`
      : "(playlist created — check your library)";
    const um = r.unmatched && r.unmatched.length ? ` · ${r.unmatched.length} unmatched` : "";
    status.innerHTML = `✓ ${r.matched} tracks · ${link}${um}`;
  } catch (e) {
    status.textContent = `apple export failed: ${e.message || e}`;
  }
}

// ============================================================================
// Tracklist
// ============================================================================
let filteredTracks = [];
function renderTracklist() {
  const q = searchInput.value.trim().toLowerCase();
  const onlyVis = onlyVisibleCb.checked;
  filteredTracks = DATA.merged.filter(tk => {
    if (q && !(tk.artists+" "+tk.title+" "+(tk.album||"")+" "+(tk.label||"")).toLowerCase().includes(q)) return false;
    if (onlyVis && (tk.start < viewStart || tk.start > viewEnd)) return false;
    return true;
  });
  trackCount.textContent = `${filteredTracks.length}/${DATA.merged.length}`;
  let html = "";
  for (const tk of filteredTracks) {
    const scoreClass = tk.score >= 100 ? "s100" : (tk.score >= 80 ? "s80" : (tk.score >= 40 ? "s50" : "low"));
    const inView = tk.start >= viewStart && tk.start <= viewEnd ? "in-view" : "";
    const links = [];
    if (tk.spotify) links.push(`<a href="https://open.spotify.com/track/${tk.spotify}" target="_blank" onclick="event.stopPropagation()">sp</a>`);
    if (tk.apple) links.push(`<a href="https://music.apple.com/song/${tk.apple}" target="_blank" onclick="event.stopPropagation()">am</a>`);
    if (tk.youtube) links.push(`<a href="https://youtu.be/${tk.youtube}" target="_blank" onclick="event.stopPropagation()">yt</a>`);
    html += `<div class="track-row ${inView}" data-start="${tk.start}">
      <span class="track-time">${fmt(tk.start)}</span>
      <span class="track-title"><b>${esc(tk.title)}</b><br><span class="track-title-artists">${esc(tk.artists)}</span></span>
      <span class="track-score ${scoreClass}">${tk.score}</span>
      <span class="track-hits">${tk.hits}×</span>
      <span class="track-links">${links.join(" ")}</span>
    </div>`;
  }
  tracklistBody.innerHTML = html;
  tracklistBody.querySelectorAll(".track-row").forEach(el => {
    el.addEventListener("click", () => seek(parseFloat(el.dataset.start)));
  });
  highlightActiveTrack();
}
let _lastActiveStart = null;
function highlightActiveTrack(opts = {}) {
  const t = audio.currentTime;
  tracklistBody.querySelectorAll(".track-row").forEach(r => r.classList.remove("active"));
  let active = null;
  for (const tr of filteredTracks) {
    if (tr.start <= t) active = tr;
    else break;
  }
  if (active) {
    const el = tracklistBody.querySelector(`[data-start="${active.start}"]`);
    if (el) {
      el.classList.add("active");
      if (_lastActiveStart !== active.start || opts.force) {
        el.scrollIntoView({ block: "nearest", behavior: "smooth" });
        _lastActiveStart = active.start;
      }
    }
  }
}
function scrollTracklistToTime(t) {
  let nearest = null;
  for (const tr of filteredTracks) {
    if (tr.start <= t) nearest = tr;
    else break;
  }
  if (nearest) {
    const el = tracklistBody.querySelector(`[data-start="${nearest.start}"]`);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

// ============================================================================
// Transport
// ============================================================================
function seek(t) {
  audio.currentTime = Math.max(0, Math.min(DATA.duration_s - 0.1, t));
  updateAll();
}
function updateAll() {
  const t = audio.currentTime;
  timeDisplay.textContent = `${fmt(t)} / ${fmt(DATA.duration_s)}`;
  const bpm = bpmAt(t);
  bpmNow.textContent = bpm ? `${bpm.toFixed(1)} BPM` : "— BPM";
  const key = keyAt(t);
  if (key) {
    keyNow.textContent = `${key.camelot} · ${key.name.replace(" major","maj").replace(" minor","m")}`;
    const conf = Math.min(1, Math.max(0.2, key.corr * 1.5));
    keyNow.style.background = camelotColor(key.camelot, conf);
    keyNow.title = `correlation ${key.corr.toFixed(3)}, margin ${key.margin.toFixed(3)}`;
  } else {
    keyNow.textContent = "— key";
    keyNow.style.background = "#444";
  }
  renderNow();
  highlightActiveTrack();
  redrawAll();
}
btnPlay.onclick = () => { if (audio.paused) audio.play(); else audio.pause(); };
audio.addEventListener("play", () => btnPlay.textContent = "⏸");
audio.addEventListener("pause", () => btnPlay.textContent = "▶");
document.getElementById("btn-back10").onclick = () => seek(audio.currentTime - 10);
document.getElementById("btn-fwd10").onclick  = () => seek(audio.currentTime + 10);
document.getElementById("btn-prev").onclick = () => {
  const t = audio.currentTime;
  const prev = [...DATA.merged].reverse().find(tr => tr.start < t - 1);
  if (prev) seek(prev.start);
};
document.getElementById("btn-next").onclick = () => {
  const t = audio.currentTime;
  const next = DATA.merged.find(tr => tr.start > t + 1);
  if (next) seek(next.start);
};
document.getElementById("btn-zoom-in").onclick  = () => zoomAroundTime(audio.currentTime, 0.5);
document.getElementById("btn-zoom-out").onclick = () => zoomAroundTime(audio.currentTime, 2);
document.getElementById("btn-zoom-reset").onclick = () => setZoom(0, DATA.duration_s);
document.getElementById("btn-follow").onclick = () => setFollow(!followPlayhead);
document.getElementById("speed-select").onchange = e => { audio.playbackRate = parseFloat(e.target.value); };
document.getElementById("vol-slider").oninput = e => { audio.volume = parseFloat(e.target.value); };

// ============================================================================
// Canvas events
// ============================================================================
bpmCv.addEventListener("mousedown", e => {
  dragMode = "bpm";
  const t = timeFromCanvasX(bpmCv, e.clientX, viewStart, viewEnd);
  seek(t);
  scrollTracklistToTime(t);
});
bpmCv.addEventListener("mousemove", e => {
  if (dragMode === "bpm") seek(timeFromCanvasX(bpmCv, e.clientX, viewStart, viewEnd));
  const t = timeFromCanvasX(bpmCv, e.clientX, viewStart, viewEnd);
  const bpm = bpmAt(t);
  const w = nearestWindow(t);
  const cands = DATA.windows[w] || [];
  const top = cands[0];
  const parentRect = bpmCv.parentElement.parentElement.getBoundingClientRect();
  bpmTooltip.style.display = "block";
  bpmTooltip.style.left = (e.clientX - parentRect.left + 12) + "px";
  bpmTooltip.style.top = (e.clientY - parentRect.top + 12) + "px";
  const tkey = keyAt(t);
  let html = `<b>${fmt(t)}</b>  ${bpm ? "· "+bpm.toFixed(1)+" BPM" : ""}${tkey ? " · "+tkey.camelot+" ("+tkey.name+")" : ""}`;
  if (top) html += `<br>${esc(top.artists)} — ${esc(top.title)} (score ${top.score})`;
  else html += `<br><i>no ACR match</i>`;
  if (cands.length > 1) html += `<br>+ ${cands.length - 1} alternate${cands.length-1===1?"":"s"}`;
  bpmTooltip.innerHTML = html;
});
bpmCv.addEventListener("mouseleave", () => { bpmTooltip.style.display = "none"; });

bpmCv.addEventListener("wheel", e => {
  e.preventDefault();
  const tCursor = timeFromCanvasX(bpmCv, e.clientX, viewStart, viewEnd);
  const factor = e.deltaY > 0 ? 1.25 : 0.8;
  zoomAroundTime(tCursor, factor);
}, { passive: false });

bpmCv.addEventListener("dblclick", () => setZoom(0, DATA.duration_s));

covCv.addEventListener("mousedown", e => {
  dragMode = "cov";
  const t = timeFromCanvasX(covCv, e.clientX, viewStart, viewEnd);
  seek(t);
  scrollTracklistToTime(t);
});
covCv.addEventListener("mousemove", e => {
  if (dragMode === "cov") seek(timeFromCanvasX(covCv, e.clientX, viewStart, viewEnd));
});

keyCv.addEventListener("mousedown", e => {
  dragMode = "key";
  const t = timeFromCanvasX(keyCv, e.clientX, viewStart, viewEnd);
  seek(t);
  scrollTracklistToTime(t);
});
keyCv.addEventListener("mousemove", e => {
  if (dragMode === "key") seek(timeFromCanvasX(keyCv, e.clientX, viewStart, viewEnd));
});

function minimapTimeFromX(clientX) {
  const rect = minimapCv.getBoundingClientRect();
  const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
  return (x / rect.width) * DATA.duration_s;
}
minimapCv.addEventListener("mousedown", e => {
  const rect = minimapCv.getBoundingClientRect();
  const t = minimapTimeFromX(e.clientX);
  const x = e.clientX - rect.left;
  const xL = (viewStart / DATA.duration_s) * rect.width;
  const xR = (viewEnd / DATA.duration_s) * rect.width;
  setFollow(false);
  if (Math.abs(x - xL) < 6) dragMode = "minimap-left";
  else if (Math.abs(x - xR) < 6) dragMode = "minimap-right";
  else if (x > xL && x < xR) {
    dragMode = "minimap-pan";
    minimapDragAnchor = { grabOffset: t - viewStart, width: viewEnd - viewStart };
  } else {
    const span = viewEnd - viewStart;
    setZoom(t - span/2, t + span/2);
    dragMode = "minimap-pan";
    minimapDragAnchor = { grabOffset: span/2, width: span };
  }
});
window.addEventListener("mousemove", e => {
  if (!dragMode) return;
  if (dragMode.startsWith("minimap")) {
    const t = minimapTimeFromX(e.clientX);
    if (dragMode === "minimap-left") setZoom(Math.max(0, Math.min(viewEnd - 30, t)), viewEnd);
    else if (dragMode === "minimap-right") setZoom(viewStart, Math.min(DATA.duration_s, Math.max(viewStart + 30, t)));
    else if (dragMode === "minimap-pan") {
      const newStart = t - minimapDragAnchor.grabOffset;
      setZoom(newStart, newStart + minimapDragAnchor.width);
    }
  }
});
window.addEventListener("mouseup", () => { dragMode = null; minimapDragAnchor = null; });

document.addEventListener("keydown", e => {
  if (document.activeElement === searchInput) return;
  const step = e.shiftKey ? 60 : 10;
  if (e.key === " ")         { e.preventDefault(); if (audio.paused) audio.play(); else audio.pause(); }
  else if (e.key === "ArrowRight") { e.preventDefault(); seek(audio.currentTime + step); }
  else if (e.key === "ArrowLeft")  { e.preventDefault(); seek(audio.currentTime - step); }
  else if (e.key === ",")     { e.preventDefault(); seek(audio.currentTime - 1); }
  else if (e.key === ".")     { e.preventDefault(); seek(audio.currentTime + 1); }
  else if (e.key === "j")     { seek(audio.currentTime - 10); }
  else if (e.key === "l")     { seek(audio.currentTime + 10); }
  else if (e.key === "J")     { document.getElementById("btn-prev").click(); }
  else if (e.key === "L")     { document.getElementById("btn-next").click(); }
  else if (e.key === "k" || e.key === "K") { if (audio.paused) audio.play(); else audio.pause(); }
  else if (e.key === "Home")  { seek(0); }
  else if (e.key === "End")   { seek(DATA.duration_s - 1); }
  else if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomAroundTime(audio.currentTime, 0.5); }
  else if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomAroundTime(audio.currentTime, 2); }
  else if (e.key === "0")     { e.preventDefault(); setZoom(0, DATA.duration_s); }
  else if (e.key === "f" || e.key === "F") { e.preventDefault(); setFollow(!followPlayhead); }
});

audio.addEventListener("timeupdate", updateAll);
audio.addEventListener("loadedmetadata", () => {
  if (audio.duration && !isNaN(audio.duration) && DATA.duration_s === 0) {
    DATA.duration_s = audio.duration;
    if (viewEnd === 0) setZoom(0, DATA.duration_s);
  }
  updateAll();
});

searchInput.addEventListener("input", renderTracklist);
onlyVisibleCb.addEventListener("change", renderTracklist);
window.addEventListener("resize", redrawAll);

// ============================================================================
// Wavesurfer init — uses the existing <audio> element so playback events stay
// on the same media object the rest of the viewer reads.
// ============================================================================
function audiowaveformPeaksToWavesurfer(payload) {
  // bbc/audiowaveform JSON: {data: [min,max,min,max,...], length, bits, ...}
  // wavesurfer wants Array<channel> where each channel is an array of floats
  // per pixel. We collapse min/max → a single peak per pixel: max(|min|,|max|).
  const raw = payload.data;
  const length = payload.length;
  const channels = payload.channels || 1;
  const bits = payload.bits || 8;
  const denom = bits === 8 ? 128 : 32768;
  const ch0 = new Float32Array(length);
  // Interleaved per audiowaveform: for stereo it's [L_min, L_max, R_min, R_max, ...].
  // We always render mono, taking the max absolute amplitude across channels.
  const stride = channels * 2;
  for (let i = 0; i < length; i++) {
    let peak = 0;
    for (let c = 0; c < channels; c++) {
      const a = Math.abs(raw[i * stride + 2 * c]) / denom;
      const b = Math.abs(raw[i * stride + 2 * c + 1]) / denom;
      if (a > peak) peak = a;
      if (b > peak) peak = b;
    }
    ch0[i] = peak;
  }
  return [ch0];
}

async function initWavesurfer(durationS, peaksPayload) {
  // Filled mode (no barWidth/barGap) renders a continuous shape from the peak
  // data — looks crisper than the discrete-bar mode at our typical
  // peaks-per-pixel ratio. normalize=true uses the full vertical range
  // regardless of the loudest sample's absolute amplitude. Higher device
  // pixel ratios (retina) automatically benefit since wavesurfer reads
  // window.devicePixelRatio.
  const opts = {
    container: "#waveform",
    media: audio,
    waveColor: "rgba(77, 208, 225, 0.85)",
    progressColor: "rgba(255, 202, 40, 0.95)",
    cursorColor: "#ffca28",
    cursorWidth: 2,
    height: 96,
    interact: true,
    dragToSeek: true,
    autoCenter: false,
    fillParent: true,
    normalize: true,
  };
  if (peaksPayload) {
    opts.peaks = audiowaveformPeaksToWavesurfer(peaksPayload);
    opts.duration = durationS || peaksPayload.length * (peaksPayload.samples_per_pixel || 1) / (peaksPayload.sample_rate || 22050);
    waveformEmpty.style.display = "none";
  }
  wavesurfer = WaveSurfer.create(opts);
}

// ============================================================================
// Status pill + SSE for in-flight steps
// ============================================================================
function setStatusPill(meta) {
  const steps = meta.steps || {};
  const states = Object.values(steps);
  const inFlight = states.some(s => s === "running" || s === "pending");
  const failed = states.some(s => typeof s === "string" && s.startsWith("failed:"));
  if (inFlight) {
    statusPill.textContent = "analysing…";
    statusPill.className = "live";
  } else if (failed) {
    statusPill.textContent = `${meta.completed_steps}/${meta.total_steps} (some failed)`;
    statusPill.className = "";
  } else {
    statusPill.textContent = `${meta.completed_steps}/${meta.total_steps} done`;
    statusPill.className = "idle";
  }
}

async function reloadStep(step) {
  // Re-fetch one step's JSON and patch DATA. Returns true if we actually got new data.
  try {
    if (step === "bpm") {
      const r = await fetch(`/api/sets/${encodeURIComponent(SET_ID)}/bpm.json`, { cache: "no-store" });
      if (!r.ok) return false;
      const doc = await r.json();
      DATA.bpm = doc.data || [];
      drawBPM._segments = null;  // invalidate cached segments
    } else if (step === "key") {
      const r = await fetch(`/api/sets/${encodeURIComponent(SET_ID)}/key.json`, { cache: "no-store" });
      if (!r.ok) return false;
      const doc = await r.json();
      DATA.keys = doc.data || [];
    } else if (step === "fingerprint") {
      const r = await fetch(`/api/sets/${encodeURIComponent(SET_ID)}/tracks.json`, { cache: "no-store" });
      if (!r.ok) return false;
      const doc = await r.json();
      DATA.merged = doc.merged || [];
      DATA.windows = doc.windows || {};
      DATA.stride = doc.stride_s || DATA.stride;
      drawBPM._segments = null;
      renderTracklist();
    } else if (step === "peaks") {
      // Wavesurfer was booted without peaks (browser decoded the audio itself).
      // Tear it down and rebuild with the real peaks payload now that it exists.
      const r = await fetch(`/api/sets/${encodeURIComponent(SET_ID)}/peaks.json`, { cache: "no-store" });
      if (!r.ok) return false;
      const peaksDoc = await r.json();
      if (wavesurfer) { try { wavesurfer.destroy(); } catch (_) {} wavesurfer = null; }
      await initWavesurfer(DATA.duration_s, peaksDoc);
    }
    redrawAll();
    return true;
  } catch (_) {
    return false;
  }
}

// ============================================================================
// Boot
// ============================================================================
async function fetchJsonOr404(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return await r.json();
}

async function boot() {
  // Set audio source first so the browser can start prefetching metadata.
  audio.src = `/api/sets/${encodeURIComponent(SET_ID)}/audio`;

  const setUrl = `/api/sets/${encodeURIComponent(SET_ID)}`;
  const meta = await fetch(setUrl).then(r => {
    if (!r.ok) throw new Error(`set ${SET_ID} → ${r.status}`);
    return r.json();
  });
  DATA.title = meta.title || SET_ID;
  if (meta.duration_s) DATA.duration_s = meta.duration_s;
  if (meta.services) SERVICES = meta.services;
  document.title = `${DATA.title} — SetPlot`;
  headerTitle.textContent = DATA.title;
  headerMeta.textContent = `${meta.uploader || ""}${meta.uploader ? " · " : ""}${meta.duration_s ? fmtShort(meta.duration_s) : ""}`;
  setStatusPill(meta);
  renderExportButtons();

  const [bpmDoc, keyDoc, tracksDoc, peaksDoc] = await Promise.all([
    fetchJsonOr404(`${setUrl}/bpm.json`),
    fetchJsonOr404(`${setUrl}/key.json`),
    fetchJsonOr404(`${setUrl}/tracks.json`),
    fetchJsonOr404(`${setUrl}/peaks.json`),
  ]);

  if (bpmDoc) DATA.bpm = bpmDoc.data || [];
  if (keyDoc) DATA.keys = keyDoc.data || [];
  if (tracksDoc) {
    DATA.merged = tracksDoc.merged || [];
    DATA.windows = tracksDoc.windows || {};
    DATA.stride = tracksDoc.stride_s || DATA.stride;
  }

  if (DATA.duration_s === 0) {
    // Fall back to whatever the audio element reports once it loads.
    DATA.duration_s = 60;  // placeholder — overwritten by audio.loadedmetadata
  }
  setZoom(0, DATA.duration_s);
  setFollow(true);
  renderTracklist();
  updateAll();

  await initWavesurfer(DATA.duration_s, peaksDoc);

  // If anything is still pending/running, subscribe to the latest job stream.
  // (Phase 3 wires job_id explicitly; here we listen for status changes via polling.)
  const pending = Object.entries(meta.steps || {}).filter(([, v]) => v === "pending" || v === "running");
  if (pending.length > 0) startPolling();
}

// Lightweight polling: every 2s, refetch /api/sets/{id} and reload any newly-done steps.
// (Phase 3 supports SSE per-job; this keeps the set view live even when reopened
// after the original job completed or for sets reanalysed from the CLI.)
let _pollHandle = null;
async function startPolling() {
  const seenDone = new Set();
  const tick = async () => {
    let meta;
    try {
      const r = await fetch(`/api/sets/${encodeURIComponent(SET_ID)}`, { cache: "no-store" });
      if (!r.ok) return;
      meta = await r.json();
    } catch (_) { return; }
    setStatusPill(meta);
    for (const [step, state] of Object.entries(meta.steps || {})) {
      if (state === "done" && !seenDone.has(step)) {
        seenDone.add(step);
        await reloadStep(step);
      }
    }
    const stillRunning = Object.values(meta.steps || {}).some(s => s === "running" || s === "pending");
    if (!stillRunning) {
      clearInterval(_pollHandle);
      _pollHandle = null;
    }
  };
  // Mark already-done steps so we don't redundantly refetch.
  // (boot() already loaded them.)
  _pollHandle = setInterval(tick, 2000);
  tick();
}

boot().catch(err => {
  console.error(err);
  document.body.innerHTML = `<p style="color:#c44a4a;padding:24px;font-family:monospace;">Failed to load set: ${err.message}<br><a href="/" style="color:#4dd0e1;">← back to library</a></p>`;
});

// Auto-tick so the playhead stays fluid at 1× when timeupdate fires only ~4Hz.
setInterval(() => {
  if (!audio.paused) { drawBPM(); drawCoverage(); drawKey(); drawMinimap(); }
}, 100);
