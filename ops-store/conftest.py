"""
ops-store/conftest.py
======================
Pytest configuration for the ops-store service.

Adds the project root (D:.../OilOps-PredictiveCore) and the ops-store
service root to sys.path so that:

  - ``from shared.config import Settings`` resolves via the project root.
  - ``from app.storage_interface import StorageInterface`` resolves via the
    service root.
  - ``from ops_store.app.storage_interface import StorageInterface`` resolves
    via a sys.modules alias registered below (the filesystem directory is
    named ``ops-store`` with a hyphen, which is not a valid Python identifier;
    the alias bridges this gap without altering the directory structure).

This mirrors the convention established in ops-feature/conftest.py, extended
with the module aliasing needed for ``ops_store.*`` imports.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

# Project root: the directory that contains shared/, ops-store/, etc.
_PROJECT_ROOT = Path(__file__).parent.parent
# Service root: ops-store/ (filesystem name contains a hyphen)
_SERVICE_ROOT = Path(__file__).parent

for _path in (_PROJECT_ROOT, _SERVICE_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

# ---------------------------------------------------------------------------
# Module alias: expose the service as ``ops_store`` in sys.modules so that
# ``from ops_store.app.storage_interface import StorageInterface`` works even
# though the directory on disk is ``ops-store`` (hyphenated).
#
# Strategy: create a lightweight namespace package ``ops_store`` and point it
# at the same loader/spec that would be used for ``app.*`` imports, by
# registering ``ops_store`` -> the service root package and ``ops_store.app``
# -> ``app`` (already importable from _SERVICE_ROOT on sys.path).
# ---------------------------------------------------------------------------

if "ops_store" not in sys.modules:
    # Create a top-level namespace package for ops_store pointing at the
    # service root so that sub-package imports are resolved correctly.
    _pkg = types.ModuleType("ops_store")
    _pkg.__path__ = [str(_SERVICE_ROOT)]  # type: ignore[attr-defined]
    _pkg.__package__ = "ops_store"
    _pkg.__spec__ = importlib.util.spec_from_file_location(  # type: ignore[attr-defined]
        "ops_store",
        str(_SERVICE_ROOT / "__init__.py"),
        submodule_search_locations=[str(_SERVICE_ROOT)],
    )
    sys.modules["ops_store"] = _pkg

# Ensure ops_store.app resolves to the ``app`` sub-package already importable
# from _SERVICE_ROOT.
if "ops_store.app" not in sys.modules:
    _app = importlib.import_module("app")
    _app.__name__ = "ops_store.app"
    _app.__package__ = "ops_store.app"
    sys.modules["ops_store.app"] = _app

if "ops_store.app.storage_interface" not in sys.modules:
    _si = importlib.import_module("app.storage_interface")
    _si.__name__ = "ops_store.app.storage_interface"
    _si.__package__ = "ops_store.app"
    sys.modules["ops_store.app.storage_interface"] = _si
