"""
ops-ingest/conftest.py
======================
Pytest configuration for the ops-ingest service.

Adds the project root (D:.../OilOps-PredictiveCore) and the ops-ingest
service root to sys.path so that:

  - ``from shared.schemas.canonical import CanonicalReading`` resolves via the
    project root.
  - ``from app.schemas import IngestReading`` resolves via the service root.
  - ``from ops_ingest.contracts.events import IngestionCompletedEvent`` resolves
    via the project root (ops_ingest/ package).

This mirrors the convention used by ops-feature/conftest.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root: the directory that contains shared/, ops-ingest/, ops_ingest/, etc.
_PROJECT_ROOT = Path(__file__).parent.parent
# Service root: ops-ingest/
_SERVICE_ROOT = Path(__file__).parent

for _path in (_PROJECT_ROOT, _SERVICE_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)
