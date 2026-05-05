"""Re-apply the new two-pass fold to existing bpm.json files.

The old single local-median fold missed sustained half-tempo clusters (60+ s of
RhythmExtractor lock-on during sparse-drum breakdowns). This rewrites each
already-saved set's bpm.json in place using the new global-anchor + local pass,
without re-running essentia.

Run: uv run --with-editable . python scripts/refold_existing_bpm.py
"""

from __future__ import annotations

import json
from pathlib import Path

from setplot.config import get_settings
from setplot.pipeline.bpm import (
    fold_against_anchor,
    fold_octave_outliers,
    global_tempo_anchor,
)


def refold_one(path: Path) -> tuple[int, int, float]:
    payload = json.loads(path.read_text())
    pairs = [(float(t), float(b)) for t, b in payload.get("data", [])]
    if len(pairs) < 10:
        return 0, 0, 0.0

    anchor = global_tempo_anchor(pairs)
    after_global, n_global = (
        fold_against_anchor(pairs, anchor) if anchor > 0 else (list(pairs), 0)
    )
    after_local, n_local = fold_octave_outliers(after_global)

    if n_global == 0 and n_local == 0:
        return 0, 0, anchor

    payload["data"] = [[t, b] for t, b in after_local]
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return n_global, n_local, anchor


def main() -> None:
    root = get_settings().data_dir()
    sets = sorted(p for p in root.iterdir() if p.is_dir())
    for d in sets:
        bpm_p = d / "bpm.json"
        if not bpm_p.exists():
            continue
        n_global, n_local, anchor = refold_one(bpm_p)
        if n_global == 0 and n_local == 0:
            print(f"  {d.name}: clean (anchor={anchor:.1f})")
        else:
            print(
                f"  {d.name}: folded {n_global + n_local} pts "
                f"({n_global} global + {n_local} local) anchor={anchor:.1f}"
            )


if __name__ == "__main__":
    main()
