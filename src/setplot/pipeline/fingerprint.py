"""Scan a long audio/video file against ACRCloud and emit a timestamped tracklist.

Outputs next to the input: <file>.acr.json, <file>.tracklist.txt,
<file>.evolution.txt, <file>.tracklist.cue.

Optionally also queries AudD.io (--audd / use_audd=True) for a second opinion.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from acrcloud.recognizer import ACRCloudRecognizer

# ---------- ACRCloud request/response reference ---------------------------------------------------
# Request params we control:
#   host / access_key / access_secret  -- per-project (pick region close to you).
#   timeout                            -- seconds; 10 is a sane default, bump for flaky networks.
#   start_seconds (per call)           -- offset into the file where the 10s window begins.
#   rec_length (per call, default 10)  -- length of the window submitted to ACR. Max ~12s is
#                                         meaningful; 10s is the standard. Shorter -> cheaper but
#                                         higher miss rate around transitions.
#
# What ACR picks up from the *project/container* config in the web console (NOT per-call):
#   - Audio Engine: Fingerprinting / Cover-Song (humming) / Both. For DJ sets, enable
#     Fingerprinting; Cover-Song catches live reworks and remixes-of-remixes but is noisier.
#   - Audio Source: "Recorded Audio" (tolerant to noise) vs "Line-in" (clean rips).
#   - Music bucket(s): which of ACR's 150M+ catalogs to match against + any custom buckets you
#     uploaded. Enable every bucket you're licensed for.
#
# Response shape (on success, metadata.music[] is populated; multiple hits possible per window).
# See readme of identify_tracks.py upstream for full key-by-key breakdown.
# --------------------------------------------------------------------------------------------------


# ACR status codes that mean "no point continuing" — auth / project-config /
# quota issues that won't recover by retrying or moving to the next window.
# 3001 Missing/Invalid Access Key, 3002 Invalid Project Type,
# 3003 Limit exceeded, 3014 Invalid signature.
# (3015 "could not generate fingerprint" is per-window and recoverable; not in here.)
_FATAL_ACR_STATUS_CODES = frozenset({3001, 3002, 3003, 3014})


class FingerprintAuthError(RuntimeError):
    """Raised when ACR returns a non-recoverable auth/quota status code.

    Surfaces the underlying ACR status code so callers can show a helpful
    message ("check ACR_HOST", "you've hit the daily limit", etc.) without
    having to grep through scan_file logs.
    """


@dataclass
class Hit:
    """One recognized music row from one recognize_by_file call."""

    source: str
    window_start_s: float
    window_len_s: float
    acrid: str
    title: str
    artists: str
    artists_list: list
    album: str
    album_id_spotify: str
    album_id_deezer: str
    artist_ids_spotify: list
    artist_ids_deezer: list
    label: str
    release_date: str
    genres: str
    language: str
    score: int
    duration_ms: int
    play_offset_ms: int
    sample_begin_ms: int
    sample_end_ms: int
    db_begin_ms: int
    db_end_ms: int
    result_from: int
    isrc: str
    iswc: str
    upc: str
    spotify_id: str
    deezer_id: str
    youtube_vid: str
    musicbrainz_id: str
    apple_id: str
    composers: list
    lyricists: list
    distributors: list
    works: list
    song_link: str = ""
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def track_start_in_mix_s(self) -> float:
        return max(0.0, self.window_start_s + self.sample_begin_ms / 1000 - self.play_offset_ms / 1000)


def parse_music_row(row: dict, window_start_s: float, window_len_s: float, source: str = "acrcloud") -> Hit:
    ext = row.get("external_metadata", {}) or {}
    ids = row.get("external_ids", {}) or {}
    contributors = row.get("contributors") or {}

    def first_id(provider: str, sub: str = "track") -> str:
        p = ext.get(provider) or {}
        if isinstance(p, list):
            p = p[0] if p else {}
        node = p.get(sub) if isinstance(p, dict) else None
        if isinstance(node, dict):
            return str(node.get("id", ""))
        return ""

    def album_id(provider: str) -> str:
        p = ext.get(provider) or {}
        if isinstance(p, list):
            p = p[0] if p else {}
        node = p.get("album") if isinstance(p, dict) else None
        if isinstance(node, dict):
            return str(node.get("id", ""))
        return ""

    def artist_entries(provider: str) -> list:
        p = ext.get(provider) or {}
        if isinstance(p, list):
            p = p[0] if p else {}
        return p.get("artists", []) if isinstance(p, dict) else []

    mb = ext.get("musicbrainz") or []
    if isinstance(mb, dict):
        track_node = mb.get("track")
        mb_node: dict = track_node if isinstance(track_node, dict) else mb
        mbid = str(mb_node.get("id", ""))
    elif isinstance(mb, list) and mb:
        mbid = str((mb[0] or {}).get("id", ""))
    else:
        mbid = ""

    return Hit(
        source=source,
        window_start_s=window_start_s,
        window_len_s=window_len_s,
        acrid=row.get("acrid", ""),
        title=row.get("title", ""),
        artists=", ".join(a.get("name", "") for a in row.get("artists", []) or []),
        artists_list=row.get("artists", []) or [],
        album=(row.get("album") or {}).get("name", ""),
        album_id_spotify=album_id("spotify"),
        album_id_deezer=album_id("deezer"),
        artist_ids_spotify=artist_entries("spotify"),
        artist_ids_deezer=artist_entries("deezer"),
        label=row.get("label", ""),
        release_date=row.get("release_date", ""),
        genres=", ".join(g.get("name", "") for g in row.get("genres", []) or []),
        language=row.get("language", "") or "",
        score=int(row.get("score", 0)),
        duration_ms=int(row.get("duration_ms", 0) or 0),
        play_offset_ms=int(row.get("play_offset_ms", 0) or 0),
        sample_begin_ms=int(row.get("sample_begin_time_offset_ms", 0) or 0),
        sample_end_ms=int(row.get("sample_end_time_offset_ms", 0) or 0),
        db_begin_ms=int(row.get("db_begin_time_offset_ms", 0) or 0),
        db_end_ms=int(row.get("db_end_time_offset_ms", 0) or 0),
        result_from=int(row.get("result_from", 0) or 0),
        isrc=ids.get("isrc", ""),
        iswc=ids.get("iswc", ""),
        upc=ids.get("upc", ""),
        spotify_id=first_id("spotify"),
        deezer_id=first_id("deezer"),
        youtube_vid=(ext.get("youtube") or {}).get("vid", ""),
        musicbrainz_id=mbid,
        apple_id=first_id("applemusic"),
        composers=(contributors or {}).get("composers") or [],
        lyricists=(contributors or {}).get("lyricists") or [],
        distributors=row.get("distributors") or [],
        works=row.get("works") or [],
        raw=row,
    )


def _audd_parse_timecode(tc: str) -> int:
    """'MM:SS' -> milliseconds."""
    if not tc or ":" not in tc:
        return 0
    m, s = tc.split(":", 1)
    try:
        return (int(m) * 60 + int(s)) * 1000
    except ValueError:
        return 0


def parse_audd_result(result: dict, window_start_s: float, window_len_s: float) -> Hit:
    apple = result.get("apple_music") or {}
    spot = result.get("spotify") or {}
    deezer = result.get("deezer") or {}
    mb = result.get("musicbrainz") or []

    spot_ext_ids = (spot.get("external_ids") or {}) if isinstance(spot, dict) else {}
    spot_artists = spot.get("artists") or [] if isinstance(spot, dict) else []

    deezer_artist = deezer.get("artist") or {} if isinstance(deezer, dict) else {}
    deezer_album = deezer.get("album") or {} if isinstance(deezer, dict) else {}

    mb_entry = mb[0] if isinstance(mb, list) and mb else {}
    mbid = mb_entry.get("id", "") if isinstance(mb_entry, dict) else ""

    play_offset_ms = _audd_parse_timecode(result.get("timecode", ""))

    isrc = spot_ext_ids.get("isrc") or apple.get("isrc") or deezer.get("isrc") or ""
    title = result.get("title", "")
    artist = result.get("artist", "")

    score_proxy = 85 if (apple or spot or deezer or mbid) else 65

    return Hit(
        source="audd",
        window_start_s=window_start_s,
        window_len_s=window_len_s,
        acrid=result.get("song_link", ""),
        title=title,
        artists=artist,
        artists_list=[{"name": artist}] if artist else [],
        album=result.get("album", ""),
        album_id_spotify=((spot.get("album") or {}).get("id", "") if isinstance(spot, dict) else ""),
        album_id_deezer=str(deezer_album.get("id", "")) if deezer_album else "",
        artist_ids_spotify=[{"name": a.get("name", ""), "id": a.get("id", "")} for a in spot_artists],
        artist_ids_deezer=[{"name": deezer_artist.get("name", ""), "id": str(deezer_artist.get("id", ""))}]
        if deezer_artist
        else [],
        label=result.get("label", ""),
        release_date=result.get("release_date", ""),
        genres=", ".join(apple.get("genreNames", []) or []),
        language="",
        score=score_proxy,
        duration_ms=int(apple.get("durationInMillis") or spot.get("duration_ms") or 0),
        play_offset_ms=play_offset_ms,
        sample_begin_ms=0,
        sample_end_ms=int(window_len_s * 1000),
        db_begin_ms=play_offset_ms,
        db_end_ms=play_offset_ms + int(window_len_s * 1000),
        result_from=10,  # AudD sentinel
        isrc=isrc,
        iswc="",
        upc="",
        spotify_id=spot.get("id", "") if isinstance(spot, dict) else "",
        deezer_id=str(deezer.get("id", "")) if isinstance(deezer, dict) and deezer.get("id") else "",
        youtube_vid="",
        musicbrainz_id=mbid,
        apple_id=str(apple.get("id", "")) if apple else "",
        composers=[],
        lyricists=[],
        distributors=[],
        works=[],
        song_link=result.get("song_link", ""),
        raw=result,
    )


def audd_recognize(token: str, file_path: Path, t_seconds: float, length_seconds: float) -> dict:
    """Extract a clip and POST to AudD."""
    clip_len = min(length_seconds, 12.0)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            str(t_seconds),
            "-i",
            str(file_path),
            "-t",
            str(clip_len),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            "-acodec",
            "pcm_s16le",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    audio_bytes = proc.stdout
    resp = requests.post(
        "https://api.audd.io/",
        data={"api_token": token, "return": "apple_music,spotify,deezer,musicbrainz,napster"},
        files={"file": ("clip.wav", audio_bytes, "audio/wav")},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def scan_file(
    recognizer: ACRCloudRecognizer,
    file_path: Path,
    duration_s: float,
    stride_s: float,
    rec_length: int,
    start_s: float,
    end_s: float,
    audd_token: str | None = None,
    host_for_logs: str = "?",
) -> tuple[list[Hit], dict]:
    hits: list[Hit] = []
    observed_keys: dict[str, set] = {
        "metadata": set(),
        "music_row": set(),
        "external_metadata_providers": set(),
        "audd_result": set(),
    }
    t = start_s
    stop = min(end_s, duration_s)
    total_windows = max(1, int((stop - start_s) // stride_s) + 1)
    idx = 0

    while t < stop:
        idx += 1
        backoff = 2.0
        for _attempt in range(5):
            try:
                raw = recognizer.recognize_by_file(str(file_path), int(t), rec_length)
                break
            except Exception as e:
                print(
                    f"  [{idx}/{total_windows}] t={t:.0f}s error: {e} (retry in {backoff:.1f}s)",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                backoff *= 2
        else:
            print(f"  [{idx}/{total_windows}] t={t:.0f}s: giving up on window", file=sys.stderr)
            t += stride_s
            continue

        resp = json.loads(raw) if isinstance(raw, str) else raw
        code = (resp.get("status") or {}).get("code")
        meta = resp.get("metadata") or {}
        observed_keys["metadata"].update(meta.keys())
        music = list(meta.get("music") or [])
        for row in music + list(meta.get("humming") or []):
            observed_keys["music_row"].update(row.keys())
            observed_keys["external_metadata_providers"].update((row.get("external_metadata") or {}).keys())
        for row in meta.get("humming") or []:
            row_copy = dict(row)
            row_copy.setdefault("result_from", 2)
            music.append(row_copy)
        custom_files = meta.get("custom_files") or []
        if custom_files:
            print(
                f"        custom-bucket hits: {len(custom_files)} "
                f"titles={[c.get('title') for c in custom_files[:3]]}"
            )

        if code == 0 and music:
            window_hits = [parse_music_row(row, t, rec_length) for row in music]
            window_hits.sort(key=lambda h: -h.score)
            hits.extend(window_hits)
            header = (
                f"  [{idx}/{total_windows}] t={fmt_ts(t)} "
                f"({len(window_hits)} candidate{'s' if len(window_hits) != 1 else ''})"
            )
            print(header)
            for wh in window_hits:
                po_mm = wh.play_offset_ms // 60000
                po_ss = (wh.play_offset_ms % 60000) // 1000
                dur_mm = wh.duration_ms // 60000
                dur_ss = (wh.duration_ms % 60000) // 1000
                pos = f"track-pos {po_mm:02d}:{po_ss:02d}/{dur_mm:02d}:{dur_ss:02d}"
                print(
                    f"        score={wh.score:>3}  {pos}  {wh.artists} — {wh.title}"
                    + (f"  [ISRC {wh.isrc}]" if wh.isrc else "")
                )
        elif code == 1001:
            print(f"  [{idx}/{total_windows}] t={fmt_ts(t)} acr: no match")
        elif code in _FATAL_ACR_STATUS_CODES:
            # Auth / quota / project-config errors aren't going to recover by
            # continuing to grind through the rest of the file — every window
            # would burn another wasted call. Abort loudly.
            msg = (resp.get("status") or {}).get("msg", "?")
            raise FingerprintAuthError(
                f"ACR returned status {code} {msg!r} on window {idx}/{total_windows}. "
                f"Check ACR_HOST / ACR_ACCESS_KEY / ACR_ACCESS_SECRET — these creds "
                f"don't work against {host_for_logs!r}."
            )
        else:
            msg = (resp.get("status") or {}).get("msg", "?")
            print(f"  [{idx}/{total_windows}] t={fmt_ts(t)} acr: status={code} {msg}", file=sys.stderr)

        if audd_token:
            audd_backoff = 2.0
            aud_resp: dict = {}
            for _attempt in range(3):
                try:
                    aud_resp = audd_recognize(audd_token, file_path, t, rec_length)
                    break
                except Exception as e:
                    print(f"        audd error: {e} (retry in {audd_backoff:.1f}s)", file=sys.stderr)
                    time.sleep(audd_backoff)
                    audd_backoff *= 2
            else:
                aud_resp = {"status": "error", "error": {"error_message": "all retries failed"}}

            observed_keys["audd_result"].update(
                (aud_resp.get("result") or {}).keys() if isinstance(aud_resp.get("result"), dict) else []
            )
            if aud_resp.get("status") == "success" and aud_resp.get("result"):
                wh = parse_audd_result(aud_resp["result"], t, rec_length)
                hits.append(wh)
                po_mm = wh.play_offset_ms // 60000
                po_ss = (wh.play_offset_ms % 60000) // 1000
                dur_mm = wh.duration_ms // 60000
                dur_ss = (wh.duration_ms % 60000) // 1000
                print(
                    f"        [AUDD] track-pos {po_mm:02d}:{po_ss:02d}/{dur_mm:02d}:{dur_ss:02d}  "
                    f"{wh.artists} — {wh.title}"
                    + (f"  [ISRC {wh.isrc}]" if wh.isrc else "")
                    + (f"  {wh.song_link}" if wh.song_link else "")
                )
            elif aud_resp.get("status") == "success":
                print("        [AUDD] no match")
            else:
                err = (aud_resp.get("error") or {}).get("error_message", aud_resp.get("status"))
                print(f"        [AUDD] error: {err}", file=sys.stderr)

        t += stride_s

    return hits, observed_keys


def _track_key(h: Hit) -> str:
    return f"{h.title.strip().lower()}|{h.artists.strip().lower()}"


def dedupe_and_merge(hits: list[Hit], min_score: int = 0) -> list[dict]:
    """Collapse consecutive top-match windows that hit the same track."""
    by_window: dict[float, Hit] = {}
    for h in hits:
        if h.score < min_score:
            continue
        cur = by_window.get(h.window_start_s)
        if cur is None or h.score > cur.score:
            by_window[h.window_start_s] = h
    ordered = [by_window[k] for k in sorted(by_window)]
    merged: list[dict] = []
    for h in ordered:
        if merged and merged[-1]["_key"] == _track_key(h):
            entry = merged[-1]
            entry["last_seen_s"] = h.window_start_s
            entry["hit_count"] += 1
            if h.score > entry["best_score"]:
                entry["best_score"] = h.score
                entry["estimated_track_start_s"] = h.track_start_in_mix_s
            continue
        merged.append(
            {
                "_key": _track_key(h),
                "first_seen_s": h.window_start_s,
                "last_seen_s": h.window_start_s,
                "estimated_track_start_s": h.track_start_in_mix_s,
                "hit_count": 1,
                "best_score": h.score,
                "acrid": h.acrid,
                "title": h.title,
                "artists": h.artists,
                "album": h.album,
                "label": h.label,
                "release_date": h.release_date,
                "genres": h.genres,
                "duration_ms": h.duration_ms,
                "isrc": h.isrc,
                "upc": h.upc,
                "spotify_id": h.spotify_id,
                "deezer_id": h.deezer_id,
                "youtube_vid": h.youtube_vid,
                "musicbrainz_id": h.musicbrainz_id,
                "apple_id": h.apple_id,
                "spotify_url": f"https://open.spotify.com/track/{h.spotify_id}" if h.spotify_id else "",
                "youtube_url": f"https://youtu.be/{h.youtube_vid}" if h.youtube_vid else "",
            }
        )
    return merged


def fmt_ts(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def write_outputs(merged: list[dict], hits: list[Hit], base: Path, suffix: str = "") -> None:
    json_path = base.with_suffix(base.suffix + f"{suffix}.acr.json")
    txt_path = base.with_suffix(base.suffix + f"{suffix}.tracklist.txt")
    evo_path = base.with_suffix(base.suffix + f"{suffix}.evolution.txt")
    cue_path = base.with_suffix(base.suffix + f"{suffix}.tracklist.cue")

    json_path.write_text(
        json.dumps(
            {
                "merged": merged,
                "raw_hits": [
                    h.raw
                    | {"_window_start_s": h.window_start_s, "_track_start_in_mix_s": h.track_start_in_mix_s}
                    for h in hits
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    lines = [
        f"{fmt_ts(e['estimated_track_start_s'])}  {e['artists']} — {e['title']}"
        f"    [score {e['best_score']}, hits {e['hit_count']}]"
        + (f"  {e['spotify_url']}" if e["spotify_url"] else "")
        for e in merged
    ]
    txt_path.write_text("\n".join(lines) + "\n")

    evo_lines: list[str] = []
    by_window: dict[float, list[Hit]] = {}
    for h in hits:
        by_window.setdefault(h.window_start_s, []).append(h)
    for t_w in sorted(by_window):
        ws = by_window[t_w]
        ws.sort(key=lambda x: -x.score)
        evo_lines.append(f"\n=== {fmt_ts(t_w)}  ({len(ws)} candidate{'s' if len(ws) != 1 else ''}) ===")
        ws.sort(key=lambda x: (0 if x.source == "acrcloud" else 1, -x.score))
        for h in ws:
            po_mm, po_ss = divmod(h.play_offset_ms // 1000, 60)
            dur_mm, dur_ss = divmod(h.duration_ms // 1000, 60) if h.duration_ms else (0, 0)
            sb = h.sample_begin_ms / 1000
            se = h.sample_end_ms / 1000
            dbb = h.db_begin_ms / 1000
            dbe = h.db_end_ms / 1000
            est_start = fmt_ts(h.track_start_in_mix_s)
            if h.source == "audd":
                engine = "AUDD"
            else:
                engine = "ACR/" + {1: "fingerprint", 2: "humming/cover", 3: "fingerprint+reranked"}.get(
                    h.result_from, f"engine={h.result_from}"
                )
            evo_lines.append(
                f"  [{engine}]  score={h.score:>3}  track@{po_mm:02d}:{po_ss:02d}/{dur_mm:02d}:{dur_ss:02d}  "
                f"clip[{sb:.1f}-{se:.1f}s]  db[{dbb:.1f}-{dbe:.1f}s]  est_mix_start={est_start}  id={h.acrid[:20]}"
            )
            evo_lines.append(f"      {h.artists} — {h.title}")
            meta = []
            if h.album:
                meta.append(f"album={h.album}")
            if h.label:
                meta.append(f"label={h.label}")
            if h.release_date:
                meta.append(f"released={h.release_date}")
            if h.genres:
                meta.append(f"genres={h.genres}")
            if h.language:
                meta.append(f"lang={h.language}")
            if h.isrc:
                meta.append(f"ISRC={h.isrc}")
            if h.iswc:
                meta.append(f"ISWC={h.iswc}")
            if h.upc:
                meta.append(f"UPC={h.upc}")
            if h.distributors:
                meta.append(f"distributors={', '.join(h.distributors)}")
            if meta:
                evo_lines.append(f"      {' | '.join(meta)}")
            if h.composers:
                names = ", ".join(c.get("name", "") if isinstance(c, dict) else str(c) for c in h.composers)
                evo_lines.append(f"      composers: {names}")
            if h.lyricists:
                names = ", ".join(c.get("name", "") if isinstance(c, dict) else str(c) for c in h.lyricists)
                evo_lines.append(f"      lyricists: {names}")
            for w in h.works:
                wname = w.get("name", "?")
                wiswc = w.get("iswc", "")
                evo_lines.append(f'      work: "{wname}"' + (f" [ISWC {wiswc}]" if wiswc else ""))
                for cr in w.get("creators", []) or []:
                    roles = "/".join(cr.get("roles", []) or [])
                    evo_lines.append(f"         - {cr.get('name', '?')} ({roles}) IPI={cr.get('ipi', '')}")
            links = []
            if h.spotify_id:
                links.append(f"spotify:track:{h.spotify_id}")
            if h.album_id_spotify:
                links.append(f"spotify:album:{h.album_id_spotify}")
            if h.apple_id:
                links.append(f"applemusic:{h.apple_id}")
            if h.deezer_id:
                links.append(f"deezer:{h.deezer_id}")
            if h.album_id_deezer:
                links.append(f"deezer:album:{h.album_id_deezer}")
            if h.youtube_vid:
                links.append(f"youtu.be/{h.youtube_vid}")
            if h.musicbrainz_id:
                links.append(f"mbid:{h.musicbrainz_id}")
            if links:
                evo_lines.append(f"      {' | '.join(links)}")
            if h.artist_ids_spotify:
                ids = ", ".join(f"{a.get('name', '')}({a.get('id', '')})" for a in h.artist_ids_spotify)
                evo_lines.append(f"      spotify-artists: {ids}")
    evo_path.write_text("\n".join(evo_lines).lstrip() + "\n")

    cue_lines = ['PERFORMER "DJ Set"', f'TITLE "{base.stem}"', f'FILE "{base.name}" MP3']
    for i, e in enumerate(merged, 1):
        total = int(e["estimated_track_start_s"])
        m, s = divmod(total, 60)
        cue_lines += [
            f"  TRACK {i:02d} AUDIO",
            f'    PERFORMER "{e["artists"]}"',
            f'    TITLE "{e["title"]}"',
            f"    INDEX 01 {m:02d}:{s:02d}:00",
        ]
    cue_path.write_text("\n".join(cue_lines) + "\n")

    print(f"\nWrote:\n  {json_path}\n  {txt_path}\n  {evo_path}\n  {cue_path}")
    print(f"\n{len(merged)} unique tracks across {len(hits)} window hits")


def probe_duration(path: Path) -> float:
    out = (
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ]
        )
        .decode()
        .strip()
    )
    return float(out)


def run(
    file: Path,
    *,
    stride: float = 30.0,
    rec_length: int = 10,
    start: float = 0.0,
    end: float = float("inf"),
    suffix: str = "",
    use_audd: bool = False,
) -> Path:
    """Run ACR fingerprinting end-to-end. Returns path to the JSON output."""
    file = Path(file)
    if not file.exists():
        raise FileNotFoundError(file)

    from setplot.config import get_settings

    settings = get_settings()
    host, key, secret = settings.acr_host, settings.acr_access_key, settings.acr_access_secret
    if not (host and key and secret):
        raise RuntimeError(
            "ACR creds missing — set ACR_HOST / ACR_ACCESS_KEY / ACR_ACCESS_SECRET "
            "in your shell or a project .env. "
            "Get them at https://console.acrcloud.com -> your Audio/Video Recognition project."
        )

    recognizer = ACRCloudRecognizer({"host": host, "access_key": key, "access_secret": secret, "timeout": 15})

    duration = probe_duration(file)
    print(f"File: {file}  duration={fmt_ts(duration)}")
    print(
        f"Stride={stride}s  rec_length={rec_length}s  range={fmt_ts(start)}..{fmt_ts(min(duration, end))}\n"
    )

    audd_token = settings.audd_token if use_audd else None
    if use_audd and not audd_token:
        raise RuntimeError("--audd requires AUDD_TOKEN env var.")

    hits, observed = scan_file(
        recognizer,
        file,
        duration,
        stride,
        rec_length,
        start,
        end,
        audd_token=audd_token,
        host_for_logs=host,
    )
    merged = dedupe_and_merge(hits)
    write_outputs(merged, hits, file, suffix=suffix)
    print("\n=== Response key coverage ===")
    print(f"ACR metadata keys seen:          {sorted(observed['metadata'])}")
    print(f"ACR music-row keys seen:         {sorted(observed['music_row'])}")
    print(f"ACR external_metadata providers: {sorted(observed['external_metadata_providers'])}")
    if audd_token:
        print(f"AudD result keys seen:           {sorted(observed['audd_result'])}")
    return file.with_suffix(file.suffix + f"{suffix}.acr.json")
