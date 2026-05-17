"""Pytest configuration for the ops-models service.

Registers all internal modules under fully-qualified ``ops_models.*`` names
using ``importlib.util.spec_from_file_location`` so that tests can import
them without adding SERVICE_ROOT to ``sys.path`` globally (which would cause
namespace collision with the plain ``app`` package name used by other services
in this monorepo).

pytest.ini at the repository root MUST include ``addopts = --import-mode=importlib``.
"""

import importlib.util
import sys
import types
from pathlib import Path

_SERVICE_ROOT = Path(__file__).parent


def _register_module(name: str, path: Path) -> None:
    """Register *path* in ``sys.modules`` under *name* if not already present."""
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


# Register ``ops_models`` as a namespace package first so that all
# ``ops_models.*`` sub-module registrations below resolve the parent correctly.
if "ops_models" not in sys.modules:
    ns_pkg = types.ModuleType("ops_models")
    ns_pkg.__path__ = [str(_SERVICE_ROOT)]  # type: ignore[assignment]
    ns_pkg.__package__ = "ops_models"
    sys.modules["ops_models"] = ns_pkg

_register_module(
    "ops_models.app",
    _SERVICE_ROOT / "app" / "__init__.py",
)
_register_module(
    "ops_models.app.serving",
    _SERVICE_ROOT / "app" / "serving" / "__init__.py",
)
_register_module(
    "ops_models.app.serving.onnx_runner",
    _SERVICE_ROOT / "app" / "serving" / "onnx_runner.py",
)
_register_module(
    "ops_models.app.serving.model_registry",
    _SERVICE_ROOT / "app" / "serving" / "model_registry.py",
)
