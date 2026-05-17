"""Pytest configuration for the ops-api service.

Registers all internal modules under fully-qualified ``ops_api.*`` names
using ``importlib.util.spec_from_file_location`` so that tests can import
them without adding SERVICE_ROOT to ``sys.path`` globally (which would cause
namespace collision with the plain ``app`` package name used by other services
in this monorepo).

pytest.ini at the repository root MUST include ``addopts = --import-mode=importlib``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_SERVICE_ROOT = Path(__file__).parent
_APP_DIR = _SERVICE_ROOT / "app"

# Insert project root so ``shared.*`` is resolvable in test sessions.
_path_str = str(_PROJECT_ROOT)
if _path_str not in sys.path:
    sys.path.insert(0, _path_str)


def _register_module(name: str, path: Path) -> None:
    """Register *path* in ``sys.modules`` under *name* if not already present."""
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


# Register ``ops_api`` as a namespace package so that all
# ``ops_api.*`` sub-module registrations below resolve the parent correctly.
if "ops_api" not in sys.modules:
    ns_pkg = types.ModuleType("ops_api")
    ns_pkg.__path__ = [str(_SERVICE_ROOT)]  # type: ignore[assignment]
    ns_pkg.__package__ = "ops_api"
    sys.modules["ops_api"] = ns_pkg

_register_module("ops_api.app", _APP_DIR / "__init__.py")
_register_module("ops_api.app.middleware", _APP_DIR / "middleware" / "__init__.py")
_register_module("ops_api.app.middleware.advisory", _APP_DIR / "middleware" / "advisory.py")
_register_module("ops_api.app.middleware.tracing", _APP_DIR / "middleware" / "tracing.py")
_register_module("ops_api.app.main", _APP_DIR / "main.py")
