"""
ops-cli/conftest.py
===================
pytest configuration for the ops-cli package.

Adds the ``ops-cli/`` directory to ``sys.path`` so that ``main.py``
(which lives directly in that directory) is importable as ``main``
by the test suite — works around the fact that ``ops-cli`` uses a
hyphen which is not valid as a Python package identifier.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``import main`` (and ``from main import app``) resolve to
# ops-cli/main.py regardless of the working directory from which
# pytest is invoked.
_CLI_DIR = Path(__file__).parent
if str(_CLI_DIR) not in sys.path:
    sys.path.insert(0, str(_CLI_DIR))
