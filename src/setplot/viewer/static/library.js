// Library landing — list known sets, kick off a new ingest with live status.

const grid = document.getElementById("grid");
const count = document.getElementById("count");
const form = document.getElementById("import-form");
const targetInput = document.getElementById("import-target");
const submitBtn = document.getElementById("import-submit");
const statusEl = document.getElementById("import-status");

function fmtDur(seconds) {
  if (!seconds || seconds <= 0) return "?";
  const t = Math.floor(seconds);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtDate(iso) {
  if (!iso) return "?";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch (_) {
    return "?";
  }
}

function renderCard(card) {
  const completed = card.completed_steps;
  const total = card.total_steps || 1;
  const pct = Math.round((completed / total) * 100);
  const progClass = completed === total ? "" : completed === 0 ? "empty" : "partial";

  const a = document.createElement("a");
  a.className = "card";
  a.href = `/set.html?id=${encodeURIComponent(card.set_id)}`;
  a.innerHTML = `
    <div class="thumb"
         style="background-image:url('/api/sets/${encodeURIComponent(card.set_id)}/thumbnail');"
         data-fallback="no thumbnail"></div>
    <div class="card-title" title="${escapeHtml(card.title)}">${escapeHtml(card.title)}</div>
    <div class="card-meta">
      <span>${fmtDur(card.duration_s)}</span>
      <span>${escapeHtml(card.uploader || "—")}</span>
      <span>${fmtDate(card.ingested_at)}</span>
    </div>
    <div class="progress ${progClass}"><div class="progress-fill" style="width:${pct}%"></div></div>
    <div class="card-meta">
      <span>${completed}/${total} steps done</span>
      <span style="font-size:10px;">${escapeHtml(card.set_id)}</span>
    </div>`;
  // Thumbnail fallback: if the image request errors, paint a placeholder label.
  const thumb = a.querySelector(".thumb");
  const probe = new Image();
  probe.src = `/api/sets/${encodeURIComponent(card.set_id)}/thumbnail`;
  probe.onerror = () => {
    thumb.style.backgroundImage = "none";
    thumb.textContent = "no thumbnail";
  };
  return a;
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function refresh() {
  try {
    const r = await fetch("/api/sets", { cache: "no-store" });
    const body = await r.json();
    const sets = body.sets || [];
    grid.innerHTML = "";
    if (sets.length === 0) {
      grid.innerHTML = `<div class="empty-state">No sets yet. Drop a YouTube URL or a local file path above, or run <code>setplot import &lt;url-or-path&gt;</code> from the CLI.</div>`;
    } else {
      for (const s of sets) grid.appendChild(renderCard(s));
    }
    count.textContent = sets.length === 1 ? "1 set" : `${sets.length} sets`;
  } catch (err) {
    grid.innerHTML = `<div class="empty-state" style="color:#c44a4a;">Failed to load library: ${escapeHtml(String(err))}</div>`;
  }
}

function logStatus(line) {
  statusEl.classList.add("show");
  statusEl.textContent += line + "\n";
  statusEl.scrollTop = statusEl.scrollHeight;
}

async function startImport(target) {
  statusEl.classList.add("show");
  statusEl.textContent = "";
  logStatus(`POST /api/ingest target=${target}`);
  submitBtn.disabled = true;
  try {
    const r = await fetch("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, key_engine: "librosa" }),
    });
    if (!r.ok) {
      logStatus(`! ${r.status} ${await r.text()}`);
      return;
    }
    const { job_id } = await r.json();
    logStatus(`job_id=${job_id} — streaming progress…`);
    const es = new EventSource(`/api/jobs/${encodeURIComponent(job_id)}/stream`);
    let lastSetId = null;
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (ev.set_id) lastSetId = ev.set_id;
        const tag = ev.step ? `[${ev.step}]`.padEnd(14) : "[?]          ";
        const state = ev.state || "?";
        const extra = ev.error ? `  ${ev.error}` : "";
        logStatus(`  ${tag} ${state}${extra}`);
        if (ev.step === "all" && ev.state === "done") {
          es.close();
          refresh();
          if (lastSetId) {
            logStatus(`✓ done. Open: /set.html?id=${lastSetId}`);
          }
        }
      } catch (err) {
        logStatus(`(parse error: ${err})`);
      }
    };
    es.onerror = () => {
      es.close();
      refresh();
    };
    es.addEventListener("close", () => {
      es.close();
      refresh();
    });
  } finally {
    submitBtn.disabled = false;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const target = targetInput.value.trim();
  if (!target) return;
  targetInput.value = "";
  startImport(target);
});

refresh();
