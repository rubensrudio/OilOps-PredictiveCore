"""
ops-ingest/conftest.py
======================
Pytest configuration for the ops-ingest service.

Adds the project root (D:.../OilOps-PredictiveCore) to sys.path so that:

  - ``from ops_ingest.contracts.events import IngestionCompletedEvent`` resolves
    via the project root (ops_ingest/ package).
  - ``from shared.config import Settings`` resolves via the project root.

This mirrors the convention used by ops-feature/conftest.py, where sys.path is
set to the project root to avoid requiring additional environment variables when
running pytest from the project root.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root: the directory that contains shared/, ops-ingest/, ops_ingest/, etc.
_PROJECT_ROOT = Path(__file__).parent.parent

_path_str = str(_PROJECT_ROOT)
if _path_str not in sys.path:
    sys.path.insert(0, _path_str)
