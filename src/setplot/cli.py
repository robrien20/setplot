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
        typer.echo(f"setplot {__version__}")
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


@app.command()
def serve(
    port: int = typer.Option(8765, help="Port to bind."),
) -> None:
    """Serve the viewer via Python's stdlib HTTP server (Phase 1 placeholder).

    Phase 3 replaces this with a FastAPI app that provides per-set routing,
    SSE-streaming analysis, and HTTP Range audio playback.
    """
    import http.server
    import socketserver

    import setplot

    viewer_path = str(Path(setplot.__file__).parent / "viewer")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=viewer_path, **kwargs)

    typer.echo(f"Serving {viewer_path} on http://localhost:{port}/viewer.html (Ctrl-C to stop)")
    with socketserver.TCPServer(("", port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            typer.echo("\nstopping.")


if __name__ == "__main__":
    app()
