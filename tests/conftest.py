"""Test bootstrap.

- Force matplotlib to a non-interactive backend so plot tests don't try to open
  windows in CI.
- Expose ``FIXTURES`` as the canonical fixture dir.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

FIXTURES = Path(__file__).parent / "fixtures"
