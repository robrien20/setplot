"""Pre-decode cache: pass-through for native formats, ffmpeg-cache for the rest."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from setplot.pipeline import _decode

from .conftest import FIXTURES


def test_native_format_is_passed_through(tmp_path):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"\x00")  # contents irrelevant — we just check the path
    assert _decode.ensure_decoded_wav(src) == src
    # Native pass-through ⇒ no work file gets created.
    assert not (tmp_path / ".work-22050.wav").exists()


def test_native_extensions_cover_common_audio(tmp_path):
    for ext in (".wav", ".flac", ".ogg", ".oga", ".mp3"):
        src = tmp_path / f"x{ext}"
        src.write_bytes(b"\x00")
        assert _decode.ensure_decoded_wav(src).suffix == ext


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_non_native_format_is_decoded_to_cached_wav(tmp_path):
    """An m4a source produces a sibling ``.work-22050.wav`` that soundfile can read."""
    import soundfile as sf

    # Build a small m4a from the bundled mp3 fixture.
    m4a = tmp_path / "source.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(FIXTURES / "clip30.mp3"),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-y",
            str(m4a),
        ],
        check=True,
        capture_output=True,
    )

    decoded = _decode.ensure_decoded_wav(m4a, sr=22050)
    assert decoded == tmp_path / ".work-22050.wav"
    assert decoded.exists() and decoded.stat().st_size > 0

    info = sf.info(str(decoded))
    assert info.samplerate == 22050
    assert info.channels == 1
    assert info.subtype.startswith("PCM")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_decode_is_idempotent(tmp_path, monkeypatch):
    """Second call hits the cache — no ffmpeg invocation."""
    m4a = tmp_path / "source.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(FIXTURES / "clip30.mp3"),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-y",
            str(m4a),
        ],
        check=True,
        capture_output=True,
    )
    first = _decode.ensure_decoded_wav(m4a)
    first_mtime = first.stat().st_mtime

    # Replace subprocess.run with a sentinel that explodes if called again.
    def boom(*_a, **_kw):
        raise AssertionError("ensure_decoded_wav re-invoked ffmpeg even with cache present")

    monkeypatch.setattr(_decode, "subprocess", type("S", (), {"run": staticmethod(boom)}))
    second = _decode.ensure_decoded_wav(m4a)
    assert second == first
    assert second.stat().st_mtime == first_mtime


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_different_sample_rates_get_separate_caches(tmp_path):
    m4a = tmp_path / "source.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(FIXTURES / "clip30.mp3"),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-y",
            str(m4a),
        ],
        check=True,
        capture_output=True,
    )
    a = _decode.ensure_decoded_wav(m4a, sr=22050)
    b = _decode.ensure_decoded_wav(m4a, sr=44100)
    assert a != b
    assert a.exists() and b.exists()
