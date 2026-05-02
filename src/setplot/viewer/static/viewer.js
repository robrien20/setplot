// ============================================================================
// Data & state
// ============================================================================
let DATA = null;
let viewStart = 0;      // visible time range (seconds)
let viewEnd = 0;
let dragMode = null;    // 'bpm', 'scrub', 'minimap', 'minimap-left', 'minimap-right', 'minimap-pan'
let minimapDragAnchor = null;  // {grabOffset, widthAtGrab}
let followPlayhead = true;   // auto-pan so playhead stays centered while playing

const audio         = document.getElementById('audio');
const minimapCv     = document.getElementById('minimap-canvas');
const bpmCv         = document.getElementById('bpm-canvas');
const covCv         = document.getElementById('coverage-canvas');
const keyCv         = document.getElementById('key-canvas');
const scrubCv       = document.getElementById('scrub-canvas');
const bpmTooltip    = document.getElementById('bpm-tooltip');
const timeDisplay   = document.getElementById('time-display');
const bpmNow        = document.getElementById('bpm-now');
const keyNow        = document.getElementById('key-now');
const nowCursor     = document.getElementById('now-cursor');
const nowContent    = document.getElementById('now-content');
const tracklistBody = document.getElementById('tracklist-body');
const searchInput   = document.getElementById('search');
const trackCount    = document.getElementById('track-count');
const onlyVisibleCb = document.getElementById('only-visible');
const zoomInd       = document.getElementById('zoom-indicator');
const btnPlay       = document.getElementById('btn-play');

const GENRE_BANDS = [
  [60,  90,  '#3a4a66', 'hip-hop / downtempo'],
  [90,  110, '#3e506c', 'house slow / dub'],
  [110, 128, '#425672', 'house / tech-house'],
  [128, 140, '#465c78', 'techno / trance'],
  [140, 160, '#4a627e', 'hard techno / hardgroove'],
  [160, 180, '#4e6884', 'drum & bass / jungle'],
  [180, 220, '#526e8a', 'hardcore / gabber'],
];

function fmt(sec) {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t/3600), m = Math.floor((t%3600)/60), s = t%60;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
function fmtShort(sec) {
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t/3600), m = Math.floor((t%3600)/60), s = t%60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
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
  const [_t, camelot, name, corr, margin] = DATA.keys[lo];
  return { camelot, name, corr, margin };
}
function nearestWindow(t) { return Math.floor(t / DATA.stride) * DATA.stride; }

// Camelot color: hue from hour 1..12, value from A (dim) / B (bright).
function camelotColor(camelot, confidence = 1.0) {
  if (!camelot) return '#333';
  const hour = parseInt(camelot);
  const ring = camelot.slice(-1);
  const hue = (hour - 1) / 12;
  const sat = 0.75 * Math.max(0.2, Math.min(1, confidence));
  const val = ring === 'A' ? 0.55 : 0.85;
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
  const minSpan = 30;  // don't zoom tighter than 30s
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
  // Re-render tracklist so the in-view tints stay accurate as the view changes
  if (DATA) renderTracklist();
}
function zoomAroundTime(tCenter, factor) {
  // factor < 1 zooms IN. When following, ignore the cursor and always zoom around playhead
  // so the playhead stays perfectly centered under any zoom operation.
  if (followPlayhead) tCenter = audio.currentTime;
  const span = (viewEnd - viewStart) * factor;
  const frac = followPlayhead ? 0.5 : (tCenter - viewStart) / (viewEnd - viewStart);
  const newStart = tCenter - span * frac;
  setZoom(newStart, newStart + span);
}

// Auto-pan the view so the playhead stays at the center. Called on every tick while playing.
function autoFollow() {
  if (!followPlayhead) return;
  const span = viewEnd - viewStart;
  // If we're zoomed all the way out, follow is a no-op
  if (span >= DATA.duration_s - 1) return;
  const newStart = audio.currentTime - span / 2;
  viewStart = newStart;
  viewEnd = newStart + span;
  clampView();
}

function setFollow(v) {
  followPlayhead = v;
  const btn = document.getElementById('btn-follow');
  if (btn) {
    btn.textContent = v ? '⤓ follow' : '⬒ free';
    btn.style.background = v ? 'rgba(77,208,225,0.25)' : '';
    btn.style.borderColor = v ? 'var(--accent)' : '';
  }
}

// ============================================================================
// Canvas helpers
// ============================================================================
function rescale(c) {
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const ctx = c.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function timeFromCanvasX(canvas, clientX, start, end) {
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
  return start + (x / rect.width) * (end - start);
}

// ============================================================================
// Drawing
// ============================================================================
function drawMinimap() {
  const { ctx, w, h } = rescale(minimapCv);
  const dur = DATA.duration_s;
  const px = t => (t / dur) * w;

  // Genre bands
  for (const [lo, hi, color] of GENRE_BANDS) {
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.25;
    const yLo = h - ((lo - 60) / 160) * h;
    const yHi = h - ((hi - 60) / 160) * h;
    ctx.fillRect(0, yHi, w, yLo - yHi);
  }
  ctx.globalAlpha = 1;

  // Unknown regions
  const runs = findNoMatchRuns(60);
  ctx.fillStyle = 'rgba(196,74,74,0.2)';
  for (const [s, e] of runs) ctx.fillRect(px(s), 0, px(e)-px(s), h);

  // BPM mini line
  ctx.strokeStyle = '#e6ecf5';
  ctx.lineWidth = 0.6;
  ctx.beginPath();
  for (let i = 0; i < DATA.bpm.length; i++) {
    const [t, b] = DATA.bpm[i];
    const y = h - ((Math.min(220, Math.max(60, b)) - 60) / 160) * h;
    if (i === 0) ctx.moveTo(px(t), y); else ctx.lineTo(px(t), y);
  }
  ctx.stroke();

  // Hour ticks
  ctx.strokeStyle = 'rgba(255,255,255,0.12)';
  ctx.fillStyle = 'rgba(230,236,245,0.4)';
  ctx.font = '9px monospace';
  for (let hr = 1; hr * 3600 < dur; hr++) {
    const x = px(hr * 3600);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.fillText(`${hr}h`, x + 2, 10);
  }

  // View-window overlay
  const vx1 = px(viewStart), vx2 = px(viewEnd);
  ctx.fillStyle = 'rgba(77,208,225,0.15)';
  ctx.fillRect(vx1, 0, vx2-vx1, h);
  ctx.strokeStyle = 'rgba(77,208,225,0.9)';
  ctx.lineWidth = 2;
  ctx.strokeRect(vx1, 0.5, vx2-vx1, h-1);
  // Edge handles (small dashes)
  ctx.fillStyle = 'rgba(77,208,225,1)';
  ctx.fillRect(vx1 - 2, h/2 - 6, 4, 12);
  ctx.fillRect(vx2 - 2, h/2 - 6, 4, 12);

  // Playhead (full-height line on minimap)
  const phX = px(audio.currentTime || 0);
  ctx.strokeStyle = 'var(--accent-2)';
  ctx.strokeStyle = '#ffca28';
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(phX, 0); ctx.lineTo(phX, h); ctx.stroke();
}

function drawBPM() {
  const { ctx, w, h } = rescale(bpmCv);
  const vs = viewStart, ve = viewEnd;
  const span = ve - vs;
  const yMin = 60, yMax = 220;
  const px = t => ((t - vs) / span) * w;
  const py = b => h - ((Math.min(yMax, Math.max(yMin, b)) - yMin) / (yMax - yMin)) * h;

  // Genre bands
  for (const [lo, hi, color, label] of GENRE_BANDS) {
    ctx.fillStyle = color; ctx.globalAlpha = 0.3;
    ctx.fillRect(0, py(hi), w, py(lo) - py(hi));
  }
  ctx.globalAlpha = 1;

  // Unknown regions shading
  const runs = findNoMatchRuns(60);
  ctx.fillStyle = 'rgba(196,74,74,0.22)';
  for (const [s, e] of runs) {
    if (e < vs || s > ve) continue;
    const x1 = Math.max(0, px(s)), x2 = Math.min(w, px(e));
    if (x2 > x1) ctx.fillRect(x1, 0, x2-x1, h);
  }

  // BPM raw line (lighter, narrower)
  ctx.strokeStyle = 'rgba(170,200,255,0.35)';
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

  // Per-segment BPM bars derived from the ACR tracklist (ground truth for transitions).
  // For each identified track, we take the median BPM value across its play window —
  // that's robust to octave errors in individual frames AND preserves exact transition
  // boundaries from ACR rather than smearing them.
  if (!drawBPM._segments) drawBPM._segments = computeTrackSegments();
  ctx.lineWidth = 3;
  for (const seg of drawBPM._segments) {
    if (seg.end < vs || seg.start > ve) continue;
    const x1 = Math.max(0, px(seg.start));
    const x2 = Math.min(w, px(seg.end));
    const y = py(seg.bpm);
    // Color by ACR confidence
    ctx.strokeStyle = seg.score >= 80 ? 'rgba(60,179,113,0.9)'
                    : seg.score >= 40 ? 'rgba(255,202,40,0.9)'
                    : 'rgba(255,140,66,0.9)';
    ctx.beginPath();
    ctx.moveTo(x1, y);
    ctx.lineTo(x2, y);
    ctx.stroke();
  }

  // Rolling-MEDIAN line (robust to outliers, preserves step-changes at transitions).
  // Window is only over existing samples, so edges don't go to phantom zero.
  const k = 5;   // ±5 samples = 11-point window at 5s steps = 55s context
  ctx.strokeStyle = '#e6ecf5';
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

  // Genre labels (right edge beyond plot area using the padding)
  ctx.fillStyle = '#8798b6';
  ctx.font = '9px -apple-system, sans-serif';
  ctx.textAlign = 'left';
  for (const [lo, hi, , label] of GENRE_BANDS) {
    ctx.fillText(label, w + 4, (py(lo) + py(hi)) / 2 + 3);
  }

  // BPM grid labels (left gutter)
  ctx.textAlign = 'right';
  ctx.fillStyle = 'rgba(230,236,245,0.5)';
  ctx.font = '10px monospace';
  for (const b of [80, 100, 120, 130, 140, 160, 180, 200]) {
    ctx.fillText(b, -4, py(b) + 3);
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.beginPath(); ctx.moveTo(0, py(b)); ctx.lineTo(w, py(b)); ctx.stroke();
  }

  // Time gridlines — adapt spacing to zoom level
  const tickIntervals = [5, 10, 30, 60, 300, 600, 1800, 3600];
  const tickPx = 80;
  let tickInterval = tickIntervals[0];
  for (const ti of tickIntervals) {
    if ((ti / span) * w >= tickPx) { tickInterval = ti; break; }
    tickInterval = ti;
  }
  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.fillStyle = 'rgba(230,236,245,0.55)';
  ctx.font = '10px monospace';
  ctx.textAlign = 'left';
  const firstTick = Math.ceil(vs / tickInterval) * tickInterval;
  for (let tt = firstTick; tt <= ve; tt += tickInterval) {
    const x = px(tt);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.fillText(fmtShort(tt), x + 3, 11);
  }

  // Track start markers for tracks in view
  ctx.lineWidth = 0.6;
  for (const t of DATA.merged) {
    if (t.start < vs || t.start > ve) continue;
    const x = px(t.start);
    ctx.strokeStyle = 'rgba(255,202,40,0.5)';
    ctx.beginPath(); ctx.moveTo(x, h * 0.05); ctx.lineTo(x, h); ctx.stroke();
  }

  // Top-track labels — only show labels for tracks that fit in the visible area
  // Greedy: show if label won't overlap one already placed
  const labelPx = 140;
  const labeled = [];
  const sorted = [...DATA.merged].filter(t => t.start >= vs && t.start <= ve)
                    .sort((a,b) => b.hits - a.hits);
  ctx.font = '10px -apple-system, sans-serif';
  ctx.fillStyle = 'rgba(230,236,245,0.95)';
  ctx.textAlign = 'left';
  for (const t of sorted) {
    const x = px(t.start);
    if (labeled.some(lx => Math.abs(lx - x) < labelPx)) continue;
    const label = `${t.artists.slice(0,20)} — ${t.title.slice(0,26)}`;
    // Label box
    const y = 28;
    ctx.fillStyle = 'rgba(26,34,51,0.85)';
    const textW = ctx.measureText(label).width + 8;
    ctx.fillRect(x + 3, y - 10, textW, 14);
    ctx.strokeStyle = 'rgba(255,202,40,0.45)';
    ctx.lineWidth = 0.6;
    ctx.strokeRect(x + 3, y - 10, textW, 14);
    ctx.fillStyle = 'rgba(230,236,245,0.95)';
    ctx.fillText(label, x + 7, y);
    labeled.push(x);
    if (labeled.length > 30) break;
  }

  // Playhead
  const t = audio.currentTime || 0;
  if (t >= vs && t <= ve) {
    const phX = px(t);
    ctx.strokeStyle = '#ffca28';
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(phX, 0); ctx.lineTo(phX, h); ctx.stroke();
    ctx.fillStyle = '#ffca28';
    ctx.beginPath(); ctx.arc(phX, 6, 3.5, 0, Math.PI*2); ctx.fill();
  }
}

function drawCoverage() {
  const { ctx, w, h } = rescale(covCv);
  const vs = viewStart, ve = viewEnd;
  const span = ve - vs;
  const stride = DATA.stride;
  const firstW = Math.floor(vs / stride) * stride;
  const lastW = Math.ceil(ve / stride) * stride;
  for (let ws = firstW; ws < lastW; ws += stride) {
    const cands = DATA.windows[ws];
    let color;
    if (!cands || cands.length === 0) color = '#333c52';
    else {
      const top = cands[0].score, n = cands.length;
      if (top >= 80 && n === 1) color = '#3cb371';
      else if (top >= 80) color = '#6fc67f';
      else if (top >= 40) color = '#ffca28';
      else color = '#ff8c42';
    }
    ctx.fillStyle = color;
    const x1 = ((ws - vs) / span) * w;
    const x2 = ((ws + stride - vs) / span) * w;
    ctx.fillRect(x1, 0, Math.max(1, x2 - x1 + 1), h);
  }
  // Playhead
  const t = audio.currentTime || 0;
  if (t >= vs && t <= ve) {
    const phX = ((t - vs) / span) * w;
    ctx.fillStyle = '#ffca28'; ctx.fillRect(phX - 1, 0, 2, h);
  }
}

function drawScrub() {
  const { ctx, w, h } = rescale(scrubCv);
  const vs = viewStart, ve = viewEnd;
  const span = ve - vs;
  // Played portion background
  ctx.fillStyle = '#242e42';
  ctx.fillRect(0, 0, w, h);
  // Loaded (buffered) regions
  try {
    const buf = audio.buffered;
    ctx.fillStyle = 'rgba(77,208,225,0.25)';
    for (let i = 0; i < buf.length; i++) {
      const s = buf.start(i), e = buf.end(i);
      if (e < vs || s > ve) continue;
      const x1 = Math.max(0, ((s - vs) / span) * w);
      const x2 = Math.min(w, ((e - vs) / span) * w);
      ctx.fillRect(x1, 0, x2 - x1, h);
    }
  } catch (_) {}
  // Played portion (up to playhead)
  const t = audio.currentTime || 0;
  if (t > vs) {
    const x2 = Math.min(w, ((t - vs) / span) * w);
    ctx.fillStyle = 'rgba(77,208,225,0.6)';
    ctx.fillRect(0, 0, x2, h);
  }
  // Playhead marker
  if (t >= vs && t <= ve) {
    const phX = ((t - vs) / span) * w;
    ctx.fillStyle = '#ffca28';
    ctx.fillRect(phX - 1.5, 0, 3, h);
    // Knob
    ctx.beginPath();
    ctx.arc(phX, h/2, 6, 0, Math.PI*2);
    ctx.fillStyle = '#ffca28';
    ctx.fill();
    ctx.strokeStyle = '#0e1420';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

// For each merged ACR track: median BPM across the window [first_seen_s, last_seen_s + stride].
// Segment boundaries come from ACR (ground truth for transitions), NOT a rolling filter.
function computeTrackSegments() {
  const stride = DATA.stride;
  const segs = [];
  for (const t of DATA.merged) {
    const s = t.first !== undefined ? t.first : t.start;
    const e = (t.last !== undefined ? t.last : t.start) + stride;
    // Collect BPM values inside the segment
    const vals = [];
    for (const [ti, b] of DATA.bpm) {
      if (ti >= s && ti <= e) vals.push(b);
      if (ti > e) break;
    }
    if (vals.length === 0) continue;
    vals.sort((a,b)=>a-b);
    const bpm = vals[vals.length >> 1];  // median
    segs.push({ start: s, end: e, bpm, score: t.score, title: t.title, artists: t.artists });
  }
  return segs;
}

function findNoMatchRuns(minDuration) {
  const runs = [];
  const stride = DATA.stride;
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
  drawScrub();
}

function drawKey() {
  const { ctx, w, h } = rescale(keyCv);
  const vs = viewStart, ve = viewEnd;
  const span = ve - vs;
  if (!DATA.keys || !DATA.keys.length) return;

  // Keys are at fixed step intervals. Draw only those in the visible range.
  const step = DATA.keys.length > 1 ? (DATA.keys[1][0] - DATA.keys[0][0]) : 10;
  for (const [t, camelot, name, corr, margin] of DATA.keys) {
    if (t + step < vs || t > ve) continue;
    const x1 = ((t - vs) / span) * w;
    const x2 = ((t + step - vs) / span) * w;
    // Confidence = min of correlation*2 and margin*8 — dim low-confidence cells
    const conf = Math.min(1, Math.max(0.2, corr * 1.5));
    ctx.fillStyle = camelotColor(camelot, conf);
    ctx.fillRect(x1, 0, Math.max(1, x2 - x1 + 1), h);
  }
  // Playhead
  const tCur = audio.currentTime || 0;
  if (tCur >= vs && tCur <= ve) {
    const phX = ((tCur - vs) / span) * w;
    ctx.fillStyle = '#ffca28';
    ctx.fillRect(phX - 1, 0, 2, h);
  }
}

// ============================================================================
// Now-playing panel
// ============================================================================
function lookupLinksFor(t, bpm, context = '') {
  // External "id this track" helpers. None of these do audio fingerprinting against
  // YouTube/SoundCloud (no public service can) — but human-curated DB lookups + smart
  // text searches are what actually solves the hardest IDs.
  const bpmLabel = bpm ? ` "${Math.round(bpm)} bpm"` : '';
  const q1001 = `yuma ${fmt(t).slice(0,5)}`;
  const qGoogle = `"yuma" coachella 2026 tracklist${bpmLabel}`;
  const qReddit = `yuma coachella 2026 track ID`;
  const qTracksniff = `yuma coachella 2026`;
  const escQ = s => encodeURIComponent(s);
  return `
    <div style="margin-top:10px; padding-top:8px; border-top:1px solid var(--border); font-size:11px;">
      <div style="color:var(--fg-dim); margin-bottom:5px;">search this moment on human-curated DBs:</div>
      <div style="display:flex; flex-wrap:wrap; gap:5px;">
        <a href="https://www.1001tracklists.com/search/result.php?main_search=${escQ(q1001)}" target="_blank">1001tracklists</a>
        <a href="https://tracksniff.com/search?q=${escQ(qTracksniff)}" target="_blank">TrackSniff</a>
        <a href="https://www.mixesdb.com/db/index.php?title=Special:Search&search=${escQ('yuma coachella')}" target="_blank">MixesDB</a>
        <a href="https://www.reddit.com/r/TrackID/search/?q=${escQ(qReddit)}" target="_blank">r/TrackID</a>
        <a href="https://www.google.com/search?q=${escQ(qGoogle)}" target="_blank">Google</a>
        <a href="https://soundcloud.com/search?q=${escQ('yuma coachella 2026')}" target="_blank">SoundCloud</a>
        <a href="https://www.youtube.com/results?search_query=${escQ('yuma coachella 2026 tracklist')}" target="_blank">YouTube</a>
      </div>
      <div style="color:var(--fg-dim); margin-top:6px; font-size:10px;">
        tip: the audio file is <code>yuma_day1.m4a</code>. Extract a clip around <b>${fmt(t)}</b>
        with <code>ffmpeg -ss ${Math.floor(t)} -i yuma_day1.m4a -t 30 clip.m4a</code> and post it to r/TrackID.
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
  nowCursor.innerHTML = `window <b>${fmt(w)} → ${fmt(w + DATA.stride)}</b>  ·  ${cands.length} candidate${cands.length===1?'':'s'}` +
    (bpm ? `  ·  BPM ${bpm.toFixed(1)}` : '') +
    (key ? `  ·  <span style="background:${camelotColor(key.camelot, Math.max(0.3, key.corr*1.5))}; color:#0e1420; padding:1px 6px; border-radius:3px; font-weight:600;">${key.camelot}</span> ${key.name}` : '');

  if (cands.length === 0) {
    nowContent.innerHTML = `<div class="no-hit">NO ACR MATCH at this window.<br>Likely an unreleased edit, heavy transition, or track not in the ACR catalog.</div>`
      + lookupLinksFor(t, bpm, 'unknown');
    return;
  }
  let html = '';
  if (cands[0].score < 40) html += `<div class="weak-only">⚠ All candidates are low-confidence (&lt;40). Treat as leads.</div>`;
  for (const c of cands) {
    const cls = c.score >= 80 ? 'top' : (c.score >= 40 ? 'medium' : 'weak');
    const poMm = Math.floor(c.play_offset_ms / 60000);
    const poSs = Math.floor((c.play_offset_ms % 60000) / 1000);
    const durMm = Math.floor(c.duration_ms / 60000);
    const durSs = Math.floor((c.duration_ms % 60000) / 1000);
    const trackPos = c.duration_ms ? `${String(poMm).padStart(2,'0')}:${String(poSs).padStart(2,'0')} / ${String(durMm).padStart(2,'0')}:${String(durSs).padStart(2,'0')}` : '';
    const engine = {1:'fingerprint',2:'humming/cover',3:'fp+reranked'}[c.result_from] || '';
    const links = [];
    if (c.spotify) links.push(`<a href="https://open.spotify.com/track/${c.spotify}" target="_blank">Spotify</a>`);
    if (c.deezer) links.push(`<a href="https://www.deezer.com/track/${c.deezer}" target="_blank">Deezer</a>`);
    if (c.youtube) links.push(`<a href="https://youtu.be/${c.youtube}" target="_blank">YouTube</a>`);
    if (c.isrc) links.push(`<a href="https://musicbrainz.org/search?type=recording&query=isrc:${c.isrc}" target="_blank">MB</a>`);
    html += `<div class="candidate ${cls}">
      <div class="candidate-title">${esc(c.title)}</div>
      <div class="candidate-artist">${esc(c.artists)}</div>
      <div class="candidate-meta">score ${c.score}${engine?' · '+engine:''}${trackPos?' · track@'+trackPos:''}${c.album?' · '+esc(c.album):''}${c.label?' · '+esc(c.label):''}${c.isrc?' · ISRC '+c.isrc:''}</div>
      ${links.length ? `<div class="candidate-links">${links.join('')}</div>` : ''}
    </div>`;
  }
  // Always include manual-ID helpers — useful even for strong matches (to verify,
  // find the original vs remix, or look up what else the DJ played from that label).
  html += lookupLinksFor(t, bpm, cands[0].score < 40 ? 'weak' : 'confirmed');
  nowContent.innerHTML = html;
}
function esc(s) { return (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'})[c]); }

// ============================================================================
// Tracklist
// ============================================================================
let filteredTracks = [];
function renderTracklist() {
  const q = searchInput.value.trim().toLowerCase();
  const onlyVis = onlyVisibleCb.checked;
  filteredTracks = DATA.merged.filter(t => {
    if (q && !(t.artists+' '+t.title+' '+t.album+' '+t.label).toLowerCase().includes(q)) return false;
    if (onlyVis && (t.start < viewStart || t.start > viewEnd)) return false;
    return true;
  });
  trackCount.textContent = `${filteredTracks.length}/${DATA.merged.length}`;
  let html = '';
  for (const t of filteredTracks) {
    const scoreClass = t.score >= 100 ? 's100' : (t.score >= 80 ? 's80' : (t.score >= 40 ? 's50' : 'low'));
    const inView = t.start >= viewStart && t.start <= viewEnd ? 'in-view' : '';
    const links = [];
    if (t.spotify) links.push(`<a href="${t.spotify}" target="_blank" onclick="event.stopPropagation()">sp</a>`);
    if (t.youtube) links.push(`<a href="${t.youtube}" target="_blank" onclick="event.stopPropagation()">yt</a>`);
    html += `<div class="track-row ${inView}" data-start="${t.start}">
      <span class="track-time">${fmt(t.start)}</span>
      <span class="track-title"><b>${esc(t.title)}</b><br><span class="track-title-artists">${esc(t.artists)}</span></span>
      <span class="track-score ${scoreClass}">${t.score}</span>
      <span class="track-hits">${t.hits}×</span>
      <span class="track-links">${links.join(' ')}</span>
    </div>`;
  }
  tracklistBody.innerHTML = html;
  tracklistBody.querySelectorAll('.track-row').forEach(el => {
    el.addEventListener('click', () => seek(parseFloat(el.dataset.start)));
  });
  highlightActiveTrack();
}
let _lastActiveStart = null;
function highlightActiveTrack(opts = {}) {
  const t = audio.currentTime;
  tracklistBody.querySelectorAll('.track-row').forEach(r => r.classList.remove('active'));
  let active = null;
  for (const tr of filteredTracks) {
    if (tr.start <= t) active = tr;
    else break;
  }
  if (active) {
    const el = tracklistBody.querySelector(`[data-start="${active.start}"]`);
    if (el) {
      el.classList.add('active');
      // Auto-scroll the tracklist to keep the active track on screen — but only when
      // we actually changed active track (avoid fighting the user's manual scroll).
      if (_lastActiveStart !== active.start || opts.force) {
        el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        _lastActiveStart = active.start;
      }
    }
  }
}

// Scroll the tracklist to whichever track contains the given time (even if not the active one).
function scrollTracklistToTime(t) {
  let nearest = null;
  for (const tr of filteredTracks) {
    if (tr.start <= t) nearest = tr;
    else break;
  }
  if (nearest) {
    const el = tracklistBody.querySelector(`[data-start="${nearest.start}"]`);
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
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
  bpmNow.textContent = bpm ? `${bpm.toFixed(1)} BPM` : '— BPM';
  const key = keyAt(t);
  if (key) {
    keyNow.textContent = `${key.camelot} · ${key.name.replace(' major','maj').replace(' minor','m')}`;
    const conf = Math.min(1, Math.max(0.2, key.corr * 1.5));
    keyNow.style.background = camelotColor(key.camelot, conf);
    keyNow.title = `correlation ${key.corr.toFixed(3)}, margin ${key.margin.toFixed(3)}`;
  } else {
    keyNow.textContent = '— key';
    keyNow.style.background = '#444';
  }
  renderNow();
  highlightActiveTrack();
  redrawAll();
}
btnPlay.onclick = () => { if (audio.paused) audio.play(); else audio.pause(); };
audio.addEventListener('play', () => btnPlay.textContent = '⏸');
audio.addEventListener('pause', () => btnPlay.textContent = '▶');
document.getElementById('btn-back10').onclick = () => seek(audio.currentTime - 10);
document.getElementById('btn-fwd10').onclick  = () => seek(audio.currentTime + 10);
document.getElementById('btn-prev').onclick = () => {
  const t = audio.currentTime;
  const prev = [...DATA.merged].reverse().find(tr => tr.start < t - 1);
  if (prev) seek(prev.start);
};
document.getElementById('btn-next').onclick = () => {
  const t = audio.currentTime;
  const next = DATA.merged.find(tr => tr.start > t + 1);
  if (next) seek(next.start);
};
document.getElementById('btn-zoom-in').onclick  = () => zoomAroundTime(audio.currentTime, 0.5);
document.getElementById('btn-zoom-out').onclick = () => zoomAroundTime(audio.currentTime, 2);
document.getElementById('btn-zoom-reset').onclick = () => setZoom(0, DATA.duration_s);
document.getElementById('btn-follow').onclick = () => setFollow(!followPlayhead);
document.getElementById('speed-select').onchange = e => { audio.playbackRate = parseFloat(e.target.value); };
document.getElementById('vol-slider').oninput = e => { audio.volume = parseFloat(e.target.value); };

// ============================================================================
// Events
// ============================================================================
// BPM canvas: drag-to-seek + scroll-to-zoom + tooltip
bpmCv.addEventListener('mousedown', e => {
  dragMode = 'bpm';
  const t = timeFromCanvasX(bpmCv, e.clientX, viewStart, viewEnd);
  seek(t);
  scrollTracklistToTime(t);
});
bpmCv.addEventListener('mousemove', e => {
  if (dragMode === 'bpm') seek(timeFromCanvasX(bpmCv, e.clientX, viewStart, viewEnd));
  // Tooltip
  const t = timeFromCanvasX(bpmCv, e.clientX, viewStart, viewEnd);
  const bpm = bpmAt(t);
  const w = nearestWindow(t);
  const cands = DATA.windows[w] || [];
  const top = cands[0];
  const rect = bpmCv.getBoundingClientRect();
  const parentRect = bpmCv.parentElement.parentElement.getBoundingClientRect();
  bpmTooltip.style.display = 'block';
  bpmTooltip.style.left = (e.clientX - parentRect.left + 12) + 'px';
  bpmTooltip.style.top = (e.clientY - parentRect.top + 12) + 'px';
  const tkey = keyAt(t);
  let html = `<b>${fmt(t)}</b>  ${bpm ? '· '+bpm.toFixed(1)+' BPM' : ''}${tkey ? ' · '+tkey.camelot+' ('+tkey.name+')' : ''}`;
  if (top) html += `<br>${esc(top.artists)} — ${esc(top.title)} (score ${top.score})`;
  else html += `<br><i>no ACR match</i>`;
  if (cands.length > 1) html += `<br>+ ${cands.length - 1} alternate${cands.length-1===1?'':'s'}`;
  bpmTooltip.innerHTML = html;
});
bpmCv.addEventListener('mouseleave', () => { bpmTooltip.style.display = 'none'; });

bpmCv.addEventListener('wheel', e => {
  e.preventDefault();
  const tCursor = timeFromCanvasX(bpmCv, e.clientX, viewStart, viewEnd);
  const factor = e.deltaY > 0 ? 1.25 : 0.8;
  zoomAroundTime(tCursor, factor);
}, { passive: false });

bpmCv.addEventListener('dblclick', () => setZoom(0, DATA.duration_s));

// Coverage + scrub: click/drag to seek (no zoom on these)
covCv.addEventListener('mousedown', e => {
  dragMode = 'cov';
  const t = timeFromCanvasX(covCv, e.clientX, viewStart, viewEnd);
  seek(t);
  scrollTracklistToTime(t);
});
covCv.addEventListener('mousemove', e => {
  if (dragMode === 'cov') seek(timeFromCanvasX(covCv, e.clientX, viewStart, viewEnd));
});

keyCv.addEventListener('mousedown', e => {
  dragMode = 'key';
  const t = timeFromCanvasX(keyCv, e.clientX, viewStart, viewEnd);
  seek(t);
  scrollTracklistToTime(t);
});
keyCv.addEventListener('mousemove', e => {
  if (dragMode === 'key') seek(timeFromCanvasX(keyCv, e.clientX, viewStart, viewEnd));
});

scrubCv.addEventListener('mousedown', e => {
  dragMode = 'scrub';
  seek(timeFromCanvasX(scrubCv, e.clientX, viewStart, viewEnd));
});
scrubCv.addEventListener('mousemove', e => {
  if (dragMode === 'scrub') seek(timeFromCanvasX(scrubCv, e.clientX, viewStart, viewEnd));
});

// Minimap: click anywhere moves view window to that position; drag edges to resize; drag middle to pan
function minimapTimeFromX(clientX) {
  const rect = minimapCv.getBoundingClientRect();
  const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
  return (x / rect.width) * DATA.duration_s;
}
minimapCv.addEventListener('mousedown', e => {
  const rect = minimapCv.getBoundingClientRect();
  const t = minimapTimeFromX(e.clientX);
  const x = e.clientX - rect.left;
  const xL = (viewStart / DATA.duration_s) * rect.width;
  const xR = (viewEnd / DATA.duration_s) * rect.width;
  // Any minimap interaction = taking manual control → disable follow
  setFollow(false);
  if (Math.abs(x - xL) < 6) dragMode = 'minimap-left';
  else if (Math.abs(x - xR) < 6) dragMode = 'minimap-right';
  else if (x > xL && x < xR) {
    dragMode = 'minimap-pan';
    minimapDragAnchor = { grabOffset: t - viewStart, width: viewEnd - viewStart };
  } else {
    // Click outside window = move view to center there
    const span = viewEnd - viewStart;
    setZoom(t - span/2, t + span/2);
    dragMode = 'minimap-pan';
    minimapDragAnchor = { grabOffset: span/2, width: span };
  }
});
window.addEventListener('mousemove', e => {
  if (!dragMode) return;
  if (dragMode.startsWith('minimap')) {
    const t = minimapTimeFromX(e.clientX);
    if (dragMode === 'minimap-left') setZoom(Math.max(0, Math.min(viewEnd - 30, t)), viewEnd);
    else if (dragMode === 'minimap-right') setZoom(viewStart, Math.min(DATA.duration_s, Math.max(viewStart + 30, t)));
    else if (dragMode === 'minimap-pan') {
      const newStart = t - minimapDragAnchor.grabOffset;
      setZoom(newStart, newStart + minimapDragAnchor.width);
    }
  }
});
window.addEventListener('mouseup', () => { dragMode = null; minimapDragAnchor = null; });

// Keyboard
document.addEventListener('keydown', e => {
  if (document.activeElement === searchInput) return;
  const step = e.shiftKey ? 60 : 10;
  if (e.key === ' ')         { e.preventDefault(); if (audio.paused) audio.play(); else audio.pause(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); seek(audio.currentTime + step); }
  else if (e.key === 'ArrowLeft')  { e.preventDefault(); seek(audio.currentTime - step); }
  else if (e.key === ',')     { e.preventDefault(); seek(audio.currentTime - 1); }
  else if (e.key === '.')     { e.preventDefault(); seek(audio.currentTime + 1); }
  else if (e.key === 'j')     { seek(audio.currentTime - 10); }
  else if (e.key === 'l')     { seek(audio.currentTime + 10); }
  else if (e.key === 'J')     { document.getElementById('btn-prev').click(); }
  else if (e.key === 'L')     { document.getElementById('btn-next').click(); }
  else if (e.key === 'k' || e.key === 'K') { if (audio.paused) audio.play(); else audio.pause(); }
  else if (e.key === 'Home')  { seek(0); }
  else if (e.key === 'End')   { seek(DATA.duration_s - 1); }
  else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomAroundTime(audio.currentTime, 0.5); }
  else if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomAroundTime(audio.currentTime, 2); }
  else if (e.key === '0')     { e.preventDefault(); setZoom(0, DATA.duration_s); }
  else if (e.key === 'f' || e.key === 'F') { e.preventDefault(); setFollow(!followPlayhead); }
});

audio.addEventListener('timeupdate', updateAll);
audio.addEventListener('progress', drawScrub);  // buffered ranges update
audio.addEventListener('loadedmetadata', () => {
  if (audio.duration && !isNaN(audio.duration)) DATA.duration_s = audio.duration;
  if (viewEnd === 0) setZoom(0, DATA.duration_s);
  updateAll();
});

searchInput.addEventListener('input', renderTracklist);
onlyVisibleCb.addEventListener('change', renderTracklist);
window.addEventListener('resize', redrawAll);

// ============================================================================
// Boot
// ============================================================================
async function boot() {
  const r = await fetch('viewer_data.json');
  DATA = await r.json();
  setZoom(0, DATA.duration_s);
  setFollow(true);  // reflect initial state in the button
  renderTracklist();
  updateAll();
}
boot();

// Auto-tick so the scrub bar + playhead stay fluid at 1× when timeupdate fires only ~4Hz
setInterval(() => {
  if (!audio.paused) { drawScrub(); drawBPM(); drawCoverage(); drawKey(); drawMinimap(); }
}, 100);
