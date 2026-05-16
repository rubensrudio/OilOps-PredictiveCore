"""
ops-feature/conftest.py
========================
Pytest configuration for the ops-feature service.

Adds the project root (D:.../OilOps-PredictiveCore) and the ops-feature
service root to sys.path so that:

  - ``from shared.config import Settings`` resolves via the project root.
  - ``from app.extractors.vibration import VibrationFeatureExtractor`` resolves
    via the service root.

This mirrors the convention used by the shared/ tests, where PYTHONPATH is set
to the project root. Here we use conftest.py to avoid requiring additional
environment variables when running pytest from the project root.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root: the directory that contains shared/, ops-feature/, etc.
_PROJECT_ROOT = Path(__file__).parent.parent
# Service root: ops-feature/
_SERVICE_ROOT = Path(__file__).parent

for _path in (_PROJECT_ROOT, _SERVICE_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)
