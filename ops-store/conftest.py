"""
ops-store/conftest.py
======================
Pytest configuration for the ops-store service.

Adds the project root (D:.../OilOps-PredictiveCore) to sys.path so that
``from shared.config import Settings`` and similar cross-service imports
resolve correctly.

IMPORTANT — sys.modules["app"] isolation
-----------------------------------------
This conftest MUST NOT call ``importlib.import_module("app")`` or add the
ops-store service root to ``sys.path``.  Doing either would register
``sys.modules["app"]`` with the ops-store app package.  When pytest then
collects another service in the same session (e.g. ``ops-ingest/tests/``),
its conftest adds ``ops-ingest/`` to ``sys.path`` and expects
``sys.modules["app"]`` to resolve to ``ops-ingest/app/``.  Clobbering that
cache causes ImportError / ERRORs during test collection of the second
service (the bug fixed in this TASK-005 retry).

Fix: build the ``ops_store.*`` aliases exclusively via
``importlib.util.spec_from_file_location``, which targets specific files and
registers them only under the ``ops_store.*`` names — leaving
``sys.modules["app"]`` untouched.  The service root (ops-store/) is NOT
added to sys.path; ``--import-mode=importlib`` in pytest.ini handles module
isolation at the session level.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# Project root: the directory that contains shared/, ops-store/, etc.
_PROJECT_ROOT = Path(__file__).parent.parent
# Service root: ops-store/ (filesystem name contains a hyphen)
_SERVICE_ROOT = Path(__file__).parent
_APP_DIR = _SERVICE_ROOT / "app"

# Only the project root is added to sys.path.
# The service root (ops-store/) is intentionally NOT added: adding it would
# make ``import app`` resolve to ops-store/app and register
# sys.modules["app"] with the wrong package, breaking any subsequent service
# (e.g. ops-ingest) that also has an ``app/`` directory and tries to import
# from it.  All ops_store.* imports below are wired via spec_from_file_location
# without going through sys.path resolution.
for _path in (_PROJECT_ROOT,):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

# ---------------------------------------------------------------------------
# Register ops_store alias WITHOUT polluting sys.modules["app"]
#
# We build lightweight namespace modules for ops_store and ops_store.app,
# then use spec_from_file_location to load the concrete implementation file
# directly under the canonical ops_store.app.storage_interface name.
# ---------------------------------------------------------------------------

if "ops_store" not in sys.modules:
    ops_store_mod = ModuleType("ops_store")
    ops_store_mod.__path__ = [str(_SERVICE_ROOT)]  # type: ignore[attr-defined]
    ops_store_mod.__package__ = "ops_store"
    sys.modules["ops_store"] = ops_store_mod

if "ops_store.app" not in sys.modules:
    ops_store_app_mod = ModuleType("ops_store.app")
    ops_store_app_mod.__path__ = [str(_APP_DIR)]  # type: ignore[attr-defined]
    ops_store_app_mod.__package__ = "ops_store.app"
    sys.modules["ops_store.app"] = ops_store_app_mod

if "ops_store.app.storage_interface" not in sys.modules:
    si_spec = importlib.util.spec_from_file_location(
        "ops_store.app.storage_interface",
        str(_APP_DIR / "storage_interface.py"),
    )
    if si_spec and si_spec.loader:
        si_mod = importlib.util.module_from_spec(si_spec)
        sys.modules["ops_store.app.storage_interface"] = si_mod
        si_spec.loader.exec_module(si_mod)  # type: ignore[union-attr]
