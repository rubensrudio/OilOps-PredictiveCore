"""
ops-store/app/db -- database backend subpackage for OilOps-PredictiveCore.

Concrete storage implementations:
- ``DuckDBStore`` (TASK-006): raw_readings and feature_records via DuckDB
- ``SQLiteStore`` (TASK-007): assets, predictions, models, audit_log via SQLite

Import note
-----------
Do NOT import concrete classes at this level.  Callers must use the fully
qualified module path to avoid circular imports and to preserve the
sys.modules isolation established in conftest.py::

    from ops_store.app.db.duckdb_store import DuckDBStore
    from ops_store.app.db.sqlite_store import SQLiteStore
"""
