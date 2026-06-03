"""
ops_store/app/storage_interface.py — importable alias.

Re-exports ``StorageInterface`` from the canonical implementation at
``ops-store/app/storage_interface.py`` WITHOUT polluting sys.modules["app"].

Uses ``importlib.util.spec_from_file_location`` to load the target file
directly under the ``ops_store.app.storage_interface`` name, so that other
services whose own ``app`` package would be overwritten by a generic
``import_module("app")`` call are not affected.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_SERVICE_ROOT = Path(__file__).parent.parent.parent / "ops-store"
_APP_DIR = _SERVICE_ROOT / "app"

# Register ops_store namespace modules only when not yet present.
# Never register sys.modules["app"] — that name belongs to whichever service
# is currently running tests (ops-ingest, ops-feature, etc.).
if "ops_store" not in sys.modules:
    _pkg = ModuleType("ops_store")
    _pkg.__path__ = [str(_SERVICE_ROOT)]  # type: ignore[attr-defined]
    _pkg.__package__ = "ops_store"
    sys.modules["ops_store"] = _pkg

if "ops_store.app" not in sys.modules:
    _app_ns = ModuleType("ops_store.app")
    _app_ns.__path__ = [str(_APP_DIR)]  # type: ignore[attr-defined]
    _app_ns.__package__ = "ops_store.app"
    sys.modules["ops_store.app"] = _app_ns

if "ops_store.app.storage_interface" not in sys.modules:
    _si_spec = importlib.util.spec_from_file_location(
        "ops_store.app.storage_interface",
        str(_APP_DIR / "storage_interface.py"),
    )
    if _si_spec and _si_spec.loader:
        _si_mod = importlib.util.module_from_spec(_si_spec)
        sys.modules["ops_store.app.storage_interface"] = _si_mod
        _si_spec.loader.exec_module(_si_mod)  # type: ignore[union-attr]

from ops_store.app.storage_interface import StorageInterface  # noqa: E402, F401

__all__: list[str] = ["StorageInterface"]
