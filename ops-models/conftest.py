"""
ops-models/conftest.py
=======================
Pytest configuration for the ops-models service.

Adds ONLY the project root (D:.../OilOps-PredictiveCore) to sys.path so that:

  - ``from shared.config import Settings`` resolves via the project root.
  - ``from ops_models.app.schemas import PredictionRequest`` resolves via
    the qualified alias registered below.

Namespace isolation fix
-----------------------
Both ops-models and ops-ingest ship a top-level ``app/`` package.  When
pytest runs both services in the same session (integration wave), adding
``<service_root>`` to ``sys.path`` causes the wrong ``app`` package to be
imported by whichever service loads second.

To prevent this, ONLY ``_PROJECT_ROOT`` is inserted into sys.path.  The
``ops_models.app`` and ``ops_models.app.schemas`` aliases are registered
directly in ``sys.modules`` using ``importlib.util`` so that tests can use
the fully-qualified import path::

    from ops_models.app.schemas import PredictionResult

and remain unambiguous regardless of sys.path ordering.

The intermediate ``ops_models.app`` node is registered first with a
``__path__`` pointing at the real ``app/`` directory, satisfying the Python
import machinery requirement that parent namespace packages exist before
child modules are loaded.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PROJECT_ROOT = Path(__file__).parent.parent
_SERVICE_ROOT = Path(__file__).parent  # ops-models/
_APP_DIR = _SERVICE_ROOT / "app"

# Insert ONLY the project root so that ``shared.*`` is resolvable.
# _SERVICE_ROOT is intentionally NOT added: doing so would let
# ``import app`` resolve to ops-models/app in multi-service sessions,
# shadowing ops-ingest/app (or vice-versa).
_path_str = str(_PROJECT_ROOT)
if _path_str not in sys.path:
    sys.path.insert(0, _path_str)

# ---------------------------------------------------------------------------
# Register ops_models namespace package in sys.modules
# ---------------------------------------------------------------------------

if "ops_models" not in sys.modules:
    ops_models_mod = ModuleType("ops_models")
    sys.modules["ops_models"] = ops_models_mod

# ---------------------------------------------------------------------------
# Register ops_models.app as namespace package parent (BLOCKER 2 fix).
# The Python import machinery requires the intermediate node to exist in
# sys.modules with __path__ set before any child module (ops_models.app.*)
# can be loaded or found via qualified import.
# ---------------------------------------------------------------------------

if "ops_models.app" not in sys.modules:
    ops_models_app = ModuleType("ops_models.app")
    ops_models_app.__path__ = [str(_APP_DIR)]  # type: ignore[assignment]
    ops_models_app.__package__ = "ops_models.app"
    sys.modules["ops_models.app"] = ops_models_app

# ---------------------------------------------------------------------------
# Register ops_models.app.schemas as a concrete module loaded from disk.
# ---------------------------------------------------------------------------

if "ops_models.app.schemas" not in sys.modules:
    schemas_spec = importlib.util.spec_from_file_location(
        "ops_models.app.schemas",
        str(_APP_DIR / "schemas.py"),
    )
    if schemas_spec and schemas_spec.loader:
        schemas_mod = importlib.util.module_from_spec(schemas_spec)
        sys.modules["ops_models.app.schemas"] = schemas_mod
        schemas_spec.loader.exec_module(schemas_mod)  # type: ignore[union-attr]
