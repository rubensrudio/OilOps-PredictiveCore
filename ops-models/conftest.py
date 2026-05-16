"""
ops-models/conftest.py
=======================
Pytest configuration for the ops-models service.

Adds the project root (D:.../OilOps-PredictiveCore) and the ops-models
service root to sys.path so that:

  - ``from shared.config import Settings`` resolves via the project root.
  - ``from app.schemas import PredictionRequest`` resolves via the service root.

This follows the same convention established by ops-feature/conftest.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root: the directory that contains shared/, ops-models/, etc.
_PROJECT_ROOT = Path(__file__).parent.parent
# Service root: ops-models/
_SERVICE_ROOT = Path(__file__).parent

for _path in (_PROJECT_ROOT, _SERVICE_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)
