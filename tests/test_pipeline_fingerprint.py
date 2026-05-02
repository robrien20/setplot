"""Unit tests for the ACR result-parsing surface in `setplot.pipeline.fingerprint`.

We don't make live ACR calls in tests — credentials and network make that
flaky and expensive. Instead we feed synthesized music-row dicts modelled on
the documented response shape and assert the parsing + dedup behaviour.
"""

from __future__ import annotations

from setplot.pipeline import fingerprint as fp


def _row(title="Track A", artists="Artist 1", score=90, t=0):
    return {
        "acrid": f"acr-{title}",
        "title": title,
        "artists": [{"name": artists}],
        "album": {"name": "Some Album"},
        "label": "Some Label",
        "release_date": "2023-01-01",
        "genres": [{"name": "house"}],
        "language": "",
        "score": score,
        "duration_ms": 240_000,
        "play_offset_ms": 60_000,
        "sample_begin_time_offset_ms": 0,
        "sample_end_time_offset_ms": 10_000,
        "db_begin_time_offset_ms": 60_000,
        "db_end_time_offset_ms": 70_000,
        "result_from": 1,
        "external_ids": {"isrc": "US1234"},
        "external_metadata": {
            "spotify": {"track": {"id": "spot123"}},
            "deezer": {"track": {"id": "dz456"}},
            "youtube": {"vid": "yt789"},
            "musicbrainz": [{"id": "mb-uuid"}],
            "applemusic": {"track": {"id": "ap999"}},
        },
        "contributors": {"composers": [{"name": "Comp One"}]},
    }


def test_parse_music_row_extracts_canonical_fields():
    h = fp.parse_music_row(_row(), window_start_s=120.0, window_len_s=10.0)
    assert h.title == "Track A"
    assert h.artists == "Artist 1"
    assert h.score == 90
    assert h.window_start_s == 120.0
    assert h.spotify_id == "spot123"
    assert h.deezer_id == "dz456"
    assert h.youtube_vid == "yt789"
    assert h.musicbrainz_id == "mb-uuid"
    assert h.apple_id == "ap999"
    assert h.isrc == "US1234"
    # play_offset = 60s; sample_begin = 0; window_start = 120s
    # estimated track start = 120 + 0 - 60 = 60
    assert h.track_start_in_mix_s == 60.0


def test_audd_timecode_parser():
    assert fp._audd_parse_timecode("01:30") == 90_000
    assert fp._audd_parse_timecode("") == 0
    assert fp._audd_parse_timecode("garbage") == 0


def test_dedupe_and_merge_collapses_consecutive_same_track():
    h1 = fp.parse_music_row(_row(score=90), window_start_s=0.0, window_len_s=10.0)
    h2 = fp.parse_music_row(_row(score=92), window_start_s=10.0, window_len_s=10.0)
    h3 = fp.parse_music_row(_row(title="Track B", score=80), window_start_s=20.0, window_len_s=10.0)
    merged = fp.dedupe_and_merge([h1, h2, h3])
    assert len(merged) == 2
    track_a, track_b = merged
    assert track_a["title"] == "Track A"
    assert track_a["hit_count"] == 2
    assert track_a["best_score"] == 92
    assert track_a["first_seen_s"] == 0.0
    assert track_a["last_seen_s"] == 10.0
    assert track_b["title"] == "Track B"
    assert track_b["hit_count"] == 1


def test_dedupe_min_score_drops_weak_hits():
    h1 = fp.parse_music_row(_row(score=20), window_start_s=0.0, window_len_s=10.0)
    h2 = fp.parse_music_row(_row(score=85), window_start_s=10.0, window_len_s=10.0)
    merged = fp.dedupe_and_merge([h1, h2], min_score=70)
    assert len(merged) == 1
    assert merged[0]["best_score"] == 85
    assert merged[0]["first_seen_s"] == 10.0
