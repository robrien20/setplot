"""Typer-based CLI surface for SetPlot.

Phase 1 wires the existing analyzer scripts behind subcommands. Phase 2 adds
`setplot import / list / rm`. Phase 3 replaces `serve` with the FastAPI app +
browser auto-launch.
"""

from __future__ import annotations

from pathlib import Path

import typer

from setplot import __version__

app = typer.Typer(
    name="setplot",
    help="DJ-set analytics — BPM, key, track fingerprinting, and a streaming viewer.",
    add_completion=False,
    no_args_is_help=True,
)


def _version_cb(value: bool) -> None:
    if value:
        from setplot import _update

        typer.echo(f"setplot {__version__}")
        hint = _update.check(__version__)
        if hint:
            typer.echo(hint)
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_cb,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """SetPlot — analyse and browse DJ sets."""


@app.command()
def bpm(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    step: float = typer.Option(5.0, help="Seconds between BPM estimates."),
    window: float = typer.Option(24.0, help="Window length per estimate (s)."),
    chunk_min: float = typer.Option(10.0, "--chunk-min", help="Read this many minutes per chunk."),
    sr: int = typer.Option(22050, help="Resample rate."),
    start_bpm: float = typer.Option(130.0, "--start-bpm", help="Prior BPM center."),
) -> None:
    """Map BPM over time. Writes <file>.bpm.csv and .bpm.png next to the input."""
    from setplot.pipeline import bpm as bpm_mod

    bpm_mod.run(file, step=step, window=window, chunk_min=chunk_min, sr=sr, start_bpm=start_bpm)


@app.command()
def key(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    step: float = typer.Option(10.0),
    window: float = typer.Option(24.0),
    chunk_min: float = typer.Option(10.0, "--chunk-min"),
    sr: int = typer.Option(22050),
    engine: str = typer.Option("essentia", help="essentia (preferred) or librosa."),
) -> None:
    """Detect Camelot key over time. Writes <file>.key_<engine>.csv and .png."""
    from setplot.pipeline import key as key_mod

    if engine not in {"essentia", "librosa"}:
        raise typer.BadParameter("engine must be 'essentia' or 'librosa'")
    key_mod.run(file, step=step, window=window, chunk_min=chunk_min, sr=sr, engine=engine)


@app.command()
def identify(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    stride: float = typer.Option(30.0, help="Seconds between window starts."),
    rec_length: int = typer.Option(10, "--rec-length", help="Seconds of audio per ACR request."),
    start: float = typer.Option(0.0),
    end: float = typer.Option(float("inf")),
    suffix: str = typer.Option("", help="Append to output file names."),
    audd: bool = typer.Option(False, help="Also query AudD.io. Requires AUDD_TOKEN env var."),
) -> None:
    """Fingerprint a long file against ACRCloud. Needs ACR_HOST / ACR_ACCESS_KEY / ACR_ACCESS_SECRET."""
    from setplot.pipeline import fingerprint as fp_mod

    fp_mod.run(
        file,
        stride=stride,
        rec_length=rec_length,
        start=start,
        end=end,
        suffix=suffix,
        use_audd=audd,
    )


@app.command()
def plot(
    bpm_csv: Path = typer.Option(..., "--bpm-csv", exists=True, dir_okay=False),
    acr_json: Path = typer.Option(..., "--acr-json", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out"),
    top_labels: int = typer.Option(20, "--top-labels"),
    stride: int = typer.Option(10),
    zoom_dir: Path | None = typer.Option(None, "--zoom-dir"),
    zoom_hours: float = typer.Option(1.0, "--zoom-hours"),
) -> None:
    """Render the BPM + ACR-coverage overlay PNG (and optional per-hour zoom plots)."""
    from setplot import plotting

    plotting.plot_overlay(bpm_csv, acr_json, out, top_labels=top_labels, stride=stride)
    if zoom_dir:
        plotting.plot_hour_zoom(bpm_csv, acr_json, zoom_dir, stride=stride, hour_span=zoom_hours)


@app.command(name="import")
def import_(
    target: str = typer.Argument(..., help="YouTube/yt-dlp URL or path to a local media file."),
    no_analyze: bool = typer.Option(False, "--no-analyze", help="Just ingest; don't run analysis steps."),
    skip_fingerprint: bool = typer.Option(
        False, "--skip-fingerprint", help="Skip ACR fingerprinting (e.g. when creds aren't set)."
    ),
    skip_peaks: bool = typer.Option(
        False, "--skip-peaks", help="Skip waveform peaks (e.g. audiowaveform not installed)."
    ),
    key_engine: str = typer.Option("essentia", help="Key engine: 'essentia' or 'librosa'."),
) -> None:
    """Ingest <target> into the SetPlot data dir and run analysis."""
    from setplot.pipeline import ingest as ingest_mod
    from setplot.pipeline import orchestrator

    sid = ingest_mod.ingest(target)
    typer.echo(f"ingested → {sid}")
    if no_analyze:
        return
    skip: tuple[str, ...] = tuple(
        s for s, on in (("fingerprint", skip_fingerprint), ("peaks", skip_peaks)) if on
    )
    steps = orchestrator.analyze(sid, key_engine=key_engine, skip=skip)
    for step, state in steps.items():
        typer.echo(f"  {step:12s} {state}")


@app.command(name="list")
def list_() -> None:
    """List sets in the SetPlot data dir."""
    from setplot import store

    rows = store.list_sets()
    if not rows:
        typer.echo("(no sets — try `setplot import <url-or-path>`)")
        return
    for r in rows:
        meta = r["metadata"]
        steps = r["status"]["steps"]
        completed = sum(1 for v in steps.values() if v == "done")
        title = meta.get("title", "?")
        dur = meta.get("duration_s")
        dur_s = f"{int(dur // 60):>3}m" if dur else "  ? "
        typer.echo(f"{r['set_id']:48s}  {dur_s}  {completed}/{len(steps)} done  {title}")


@app.command(name="rm")
def rm_(set_id: str = typer.Argument(...)) -> None:
    """Delete a set from the data dir."""
    from setplot import store

    if store.delete_set(set_id):
        typer.echo(f"removed {set_id}")
    else:
        typer.echo(f"not found: {set_id}", err=True)
        raise typer.Exit(code=1)


@app.command()
def doctor() -> None:
    """Check system dependencies + ACR creds; print remediation hints."""
    from setplot import diagnostics

    rows = diagnostics.run_checks()
    failed = 0
    for r in rows:
        marker = "✓" if r.ok else "✗"
        typer.echo(f"  {marker} {r.name:18s} {r.detail}")
        if not r.ok:
            failed += 1
            if r.hint:
                typer.echo(f"      hint: {r.hint}")
    if failed:
        typer.echo(f"\n{failed} check{'s' if failed != 1 else ''} failed.")
        raise typer.Exit(code=1)
    typer.echo("\nAll checks passed.")


@app.command()
def serve(
    port: int = typer.Option(8765, help="Port to bind."),
    host: str = typer.Option("127.0.0.1", help="Host to bind."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open the browser on startup."),
) -> None:
    """Launch the FastAPI app + open the library in a browser."""
    import threading
    import time
    import webbrowser

    import uvicorn

    from setplot.server.app import create_app

    url = f"http://{host}:{port}/"

    def _open_browser_after_bind() -> None:
        # Defer the browser open by a hair so uvicorn has a chance to bind first.
        time.sleep(0.6)
        webbrowser.open(url)

    if not no_browser:
        threading.Thread(target=_open_browser_after_bind, daemon=True).start()
    typer.echo(f"SetPlot serving on {url} (Ctrl-C to stop)")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
