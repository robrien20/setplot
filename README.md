# SetPlot

Plot and analyse DJ sets. Ingest a file or YouTube URL, run BPM / key / track-fingerprint analysis once, then scroll through your collection in a browser viewer that loads instantly — waveform, identified tracks, BPM curve, Camelot key strip, all on a shared time axis with synced audio playback.

For personal use and dev users. Local-first. Single-developer maintained.

## Status

Mid-restructure. The current top-level Python scripts work standalone; we're consolidating them into an installable `setplot` package with a FastAPI server, per-set data directories, YouTube ingestion, and a streaming viewer. See [Roadmap](#roadmap) for what's done vs. planned.

## Target structure

```
SetPlot/
├── pyproject.toml              # uv-managed; defines `setplot` CLI entry point
├── uv.lock                     # checked in for reproducibility
├── .python-version             # uv-managed
├── README.md
├── .gitignore
├── .pre-commit-config.yaml     # ruff + mypy
├── .github/workflows/
│   ├── ci.yml                  # uv sync + pytest + ruff + mypy on PR
│   └── release.yml             # on git tag → build wheel + publish PyPI + GH Release
├── src/setplot/
│   ├── __init__.py             # __version__ (single source of truth)
│   ├── __main__.py             # `python -m setplot`
│   ├── cli.py                  # typer commands: serve, import, analyze, list, rm, upgrade
│   ├── config.py               # pydantic-settings: DATA_DIR, ACR_*, PORT, etc.
│   ├── store.py                # set_id generation, per-set dir layout, library scan
│   ├── server/
│   │   ├── app.py              # FastAPI app factory + browser auto-launch
│   │   ├── events.py           # SSE helpers
│   │   └── routers/
│   │       ├── library.py      # GET /api/sets, /api/sets/{id}
│   │       ├── ingest.py       # POST /api/ingest + GET /api/jobs/{id}/stream (SSE)
│   │       └── media.py        # GET /api/sets/{id}/audio (HTTP Range)
│   ├── pipeline/
│   │   ├── orchestrator.py     # runs steps in order, emits SSE progress events
│   │   ├── ingest.py           # yt-dlp wrapper: bv*[h<=720]+ba/b, harvests metadata + thumbnail
│   │   ├── peaks.py            # bbc/audiowaveform wrapper → peaks.json
│   │   ├── bpm.py              # ← bpm_over_time.py
│   │   ├── key.py              # ← key_over_time.py
│   │   └── fingerprint.py      # ← identify_tracks.py
│   ├── plotting.py             # ← plot_overlay.py (static report PNGs)
│   └── viewer/                 # served as static
│       ├── index.html          # library landing
│       ├── set.html            # individual set view
│       └── static/
│           ├── library.js
│           ├── viewer.js       # ← extracted from current viewer.html
│           ├── styles.css
│           └── vendor/
│               └── wavesurfer.esm.js   # vendored single file, no Node toolchain
├── tests/
│   ├── test_pipeline.py
│   ├── test_store.py
│   └── fixtures/30s_clip.m4a
└── archive/                    # gitignored — old iteration outputs from research phase
```

### Per-set data layout

Set data lives outside the repo at `~/Library/Application Support/SetPlot/data/` (macOS) or `$XDG_DATA_HOME/setplot/data/` (Linux), overridable via `SETPLOT_DATA_DIR`. The repo's local `data/` is gitignored for development.

```
data/{set_id}/
├── source.mp4 / source.m4a    # original media (audio-only or 720p+audio)
├── thumbnail.jpg              # from yt-dlp metadata
├── metadata.json              # title, source_url, uploader, duration, ingested_at
├── peaks.json                 # pre-computed waveform peaks (audiowaveform -z 256 -b 8)
├── bpm.json                   # streamed in by analyzer
├── key.json                   # streamed in by analyzer
├── tracks.json                # ACR fingerprint results
└── status.json                # {analysis_version, steps: {bpm:"done", key:"running", ...}}
```

The viewer reads these per-step JSONs. Once analysis completes, opening the set is instant — no recomputation.

## Stack

| Concern | Choice | Reason |
|---|---|---|
| Project mgmt | `uv` + `pyproject.toml` + `uv.lock` | 10–100× faster than pip/poetry; consensus 2026 standard |
| Layout | `src/setplot/` (PEP 621) | Prevents "tests pass locally, fail when installed" bugs |
| CLI | `typer` | Type-hint-native; standard for modern Python CLIs |
| Web | `FastAPI` + `uvicorn` | First-class SSE via `EventSourceResponse` |
| App structure | Domain-driven (routers + services + schemas) | Universal recommendation in 2025/26 FastAPI guides |
| Background work | `FastAPI BackgroundTasks` + `asyncio` | Single-user local — no Celery/Redis needed |
| Persistence | JSON files per set, scanned on demand | SQLite only when collection grows past ~50 sets |
| Settings | `pydantic-settings` | Env-var + `.env` loader, the standard |
| Lint/format/types | `ruff` + `mypy` | Ruff replaces black/isort/flake8 |
| Tests | `pytest` + small fixture clip | |
| Frontend | Vanilla JS + ES modules + vendored `wavesurfer.esm.js` | No Node toolchain; keeps install to one `uv sync` |
| Distribution | PyPI → `uv tool install setplot` | yt-dlp's pattern, minus PyInstaller binaries |
| Updates | `setplot --version` self-checks GH Releases; opt-in upgrade | yt-dlp's pattern |

## Roadmap

We ship in four PR-sized stages so the tool is always in a working state. Each stage stands on its own.

### Phase 1 — Restructure to a uv package (no behavior change)

Goal: every existing capability reachable via `uv run setplot <command>`.

- [ ] `uv init` scaffolding: `pyproject.toml`, `src/setplot/`, `uv.lock`, `.python-version`
- [ ] Move `bpm_over_time.py`, `key_over_time.py`, `identify_tracks.py`, `plot_overlay.py` into `src/setplot/pipeline/` and `src/setplot/plotting.py`
- [ ] Move `viewer.html` + `viewer_data.json` into `src/setplot/viewer/`, extract inline JS to `static/viewer.js`
- [ ] `typer` CLI wiring: `setplot bpm`, `setplot key`, `setplot identify`, `setplot plot`, `setplot serve`
- [ ] `pyproject.toml` deps + dev deps; `.pre-commit-config.yaml` (ruff + mypy)
- [ ] GitHub Actions `ci.yml` (test + lint + type-check on PR)
- [ ] Smoke test: every old behaviour reproducible against `archive/yuma_day1.bpmcopy.m4a`

### Phase 2 — Per-set data dirs + YouTube ingestion

Goal: `setplot import <youtube-url>` produces a fully analyzed set in the user data dir; `setplot list` shows the collection.

- [ ] `store.py`: `set_id` generation, per-set directory I/O, library scan
- [ ] `pipeline/ingest.py`: yt-dlp wrapper, format `bv*[h<=720][ext=mp4]+ba[ext=m4a]/b[h<=720]`, harvests title/uploader/duration/thumbnail
- [ ] `pipeline/peaks.py`: shells out to `bbc/audiowaveform -z 256 -b 8 -o peaks.json`
- [ ] `pipeline/orchestrator.py`: runs ingest → peaks → bpm / key / fingerprint, writes `status.json` after each step
- [ ] `setplot import <url>`, `setplot analyze <file>`, `setplot list`, `setplot rm <id>`
- [ ] Resolve user data dir from platform conventions or `SETPLOT_DATA_DIR`

### Phase 3 — FastAPI server + SSE + library + streaming viewer

Goal: open the viewer instantly on a set, watch panels fill in live as analysis runs (or load instantly if already done).

- [ ] `server/app.py` factory; `setplot serve` launches uvicorn + opens browser
- [ ] Routers: library (list + detail), ingest (POST + SSE stream), media (HTTP Range audio)
- [ ] Library landing page (`viewer/index.html`): cards for each set with thumbnail / title / length / BPM range / ingestion date
- [ ] Set view (`viewer/set.html`): replace the custom audio-scrub canvas with `wavesurfer.js` (loads `peaks.json` for instant render of long files); existing BPM / key / coverage / minimap canvases stay, just get fed incrementally
- [ ] SSE wiring: viewer subscribes on open, server emits `{"step":"bpm","progress":0.4,"data":[...]}` events; canvases render skeleton bars while pending
- [ ] `analysis_version` field in `status.json`; viewer surfaces "re-analyze" if outdated, never auto-runs

### Phase 4 — Release pipeline + nicer install UX

Goal: `uv tool install setplot && setplot serve` works fresh on any Mac/Linux dev machine.

- [ ] `release.yml`: on `v*` tag → `uv build` + publish to PyPI + GitHub Release with notes
- [ ] `setplot --version` checks GH Releases for newer versions; suggests `uv tool upgrade setplot`
- [ ] Optional: `homebrew-setplot` tap repo with a Formula that handles `ffmpeg` + `audiowaveform` + `yt-dlp` system deps and shells through to PyPI install
- [ ] First-run UX: `setplot doctor` checks for ffmpeg / audiowaveform / yt-dlp / ACR creds and guides setup

### Deferred (architecturally accommodated, not shipped)

- SQLite + cross-set search
- Tauri wrapper for non-dev distribution
- PyInstaller standalone binaries
- Multi-user / auth / cloud sync
- Docker

## Quick start (current scripts — pre-Phase-1)

```bash
# Fingerprint tracks (needs ACRCloud creds)
export ACR_HOST=identify-eu-west-1.acrcloud.com
export ACR_ACCESS_KEY=...
export ACR_ACCESS_SECRET=...

# Optional — streaming integrations (per-service; both can stay unset).
# Apple Music links and 30s previews work without any of these.
#
# Spotify export (PKCE; no client secret needed):
#   1. Register an app at https://developer.spotify.com/dashboard
#   2. Add `http://127.0.0.1:8765/auth/spotify/callback` as a redirect URI
#   3. Set the env var below; click "Export → Spotify" inside SetPlot to log in
export SPOTIFY_CLIENT_ID=...
#
# Apple Music export (requires a paid Apple Developer membership):
#   1. Generate an AuthKey .p8 file with MusicKit access
#   2. Set all three:
export APPLE_MUSIC_TEAM_ID=...
export APPLE_MUSIC_KEY_ID=...
export APPLE_MUSIC_KEY_PATH=/path/to/AuthKey_XXXXXXXXXX.p8
python3 identify_tracks.py mymix.m4a --stride 30 --rec-length 10

# BPM map
python3 bpm_over_time.py mymix.m4a --step 5 --window 16 --chunk-min 10

# Key map
python3 key_over_time.py mymix.m4a --step 10 --window 24

# Static overlay plot
python3 plot_overlay.py --bpm-csv mymix.m4a.bpm.csv --acr-json mymix.m4a.acr.json --out mymix.overlay.png

# Browse the existing viewer (hardcoded to yuma_day1)
./serve_viewer.sh   # → http://localhost:8765/viewer.html
```

## Quick start (target — post-Phase-3)

```bash
uv tool install setplot

setplot import https://www.youtube.com/watch?v=...      # downloads + analyzes in background
setplot serve                                            # opens library in browser

# Or analyze a local file:
setplot import ./mymix.m4a
```

## References we're patterning after

| Pattern | Reference | Stars | What we borrow |
|---|---|---|---|
| Media-download tooling | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | 160k | Use as library; pyproject + PyPI release model; GH Releases as update channel |
| Local-first FastAPI + browser UI | [severian42/GraphRAG-Local-UI](https://github.com/severian42/GraphRAG-Local-UI) | 2.3k | API + viewer split; browser auto-launch; no auth |
| Closest architectural twin | [otonomee/streamstem](https://github.com/otonomee/streamstem) | — | FastAPI + yt-dlp + heavy pipeline → downloadable artifacts |
| Modern uv-managed audio CLI | [betmoar/tracklistify](https://github.com/betmoar/tracklistify) | — | uv + pyproject; multi-format outputs; ACRCloud + Shazam fallback |
| Pre-computed waveform peaks | [bbc/audiowaveform](https://github.com/bbc/audiowaveform) + [katspaugh/wavesurfer.js](https://github.com/katspaugh/wavesurfer.js) | — | `audiowaveform -z 256 -b 8 -o peaks.json` for instant long-file waveforms |

## License

TBD.
