"""Model registry backed by a local SQLite database.

Manages versioned ONNX model artefacts for each ``asset_class``.  Uses only
the Python standard library (``sqlite3``, ``uuid``, ``datetime``) so that
``ops-models`` remains fully decoupled from ``ops-store``.

Schema
------
Table ``model_versions``:

    model_id         TEXT PRIMARY KEY          -- UUID4
    asset_class      TEXT NOT NULL
    version          TEXT NOT NULL
    artifact_path    TEXT NOT NULL
    anomaly_threshold REAL NOT NULL DEFAULT 0.5
    is_active        INTEGER NOT NULL DEFAULT 0
    deployed_at      TEXT NOT NULL             -- ISO 8601 UTC
    UNIQUE(asset_class, version)
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS model_versions (
    model_id          TEXT    NOT NULL PRIMARY KEY,
    asset_class       TEXT    NOT NULL,
    version           TEXT    NOT NULL,
    artifact_path     TEXT    NOT NULL,
    anomaly_threshold REAL    NOT NULL DEFAULT 0.5,
    is_active         INTEGER NOT NULL DEFAULT 0,
    deployed_at       TEXT    NOT NULL,
    UNIQUE(asset_class, version)
);
"""


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a :class:`sqlite3.Row` to a plain dict."""
    return dict(row)


class ModelRegistry:
    """Manage model artefact versions in a SQLite database.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Use ``":memory:"`` (the default)
        for an in-process, ephemeral database — suitable for testing.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_model(
        self,
        asset_class: str,
        version: str,
        artifact_path: str,
        anomaly_threshold: float = 0.5,
    ) -> str:
        """Register a new model version.

        The new version is inserted with ``is_active = 0``.  Call
        :meth:`activate_version` to make it the active model.

        Parameters
        ----------
        asset_class:
            Target equipment class (e.g. ``"rotating_equipment"``).
        version:
            Semantic version string (e.g. ``"1.0.0"``).
        artifact_path:
            Filesystem path (relative or absolute) to the ``.onnx`` artefact.
        anomaly_threshold:
            Score threshold above which an anomaly alert is raised.

        Returns
        -------
        str
            The ``model_id`` (UUID4) assigned to the new version.

        Raises
        ------
        sqlite3.IntegrityError
            If a model with the same ``(asset_class, version)`` already exists.
        """
        model_id = str(uuid.uuid4())
        deployed_at = datetime.now(tz=timezone.utc).isoformat()

        self._conn.execute(
            """
            INSERT INTO model_versions
                (model_id, asset_class, version, artifact_path,
                 anomaly_threshold, is_active, deployed_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (model_id, asset_class, version, artifact_path,
             anomaly_threshold, deployed_at),
        )
        self._conn.commit()
        return model_id

    def activate_version(self, model_id: str) -> None:
        """Activate a specific model version atomically.

        Within a single transaction:

        1. Verify the target version exists (raise :exc:`ValueError` if not).
        2. Deactivate **all** versions of the same ``asset_class``.
        3. Activate the target version.

        The transaction is rolled back automatically on any failure, preserving
        the previous active state.

        Parameters
        ----------
        model_id:
            The UUID of the version to activate.

        Raises
        ------
        ValueError
            If *model_id* does not exist in the registry.
        """
        # Resolve asset_class before entering the transaction so we can raise
        # ValueError without an open transaction.
        row = self._conn.execute(
            "SELECT asset_class FROM model_versions WHERE model_id = ?",
            (model_id,),
        ).fetchone()

        if row is None:
            raise ValueError(
                f"model_id '{model_id}' not found in the registry"
            )

        asset_class: str = row["asset_class"]

        # BEGIN / COMMIT / ROLLBACK managed explicitly for clarity and to allow
        # correct rollback on unexpected errors.
        try:
            self._conn.execute("BEGIN")
            # Step 1: deactivate all versions of this asset_class.
            self._execute(
                "UPDATE model_versions SET is_active = 0 WHERE asset_class = ?",
                (asset_class,),
            )
            # Step 2: activate the requested version.
            self._execute(
                "UPDATE model_versions SET is_active = 1 WHERE model_id = ?",
                (model_id,),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_active_model(self, asset_class: str) -> dict[str, Any] | None:
        """Return the currently active model for *asset_class*, or ``None``.

        Parameters
        ----------
        asset_class:
            Target equipment class.

        Returns
        -------
        dict or None
            A dict with all ``model_versions`` columns, or ``None`` if there
            is no active model for the given asset class.
        """
        row = self._conn.execute(
            """
            SELECT * FROM model_versions
            WHERE asset_class = ? AND is_active = 1
            """,
            (asset_class,),
        ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def list_models(
        self,
        asset_class: str | None = None,
    ) -> list[dict[str, Any]]:
        """List model versions ordered by ``deployed_at`` descending.

        Parameters
        ----------
        asset_class:
            When provided, filters to a specific equipment class.  When
            ``None``, all registered models are returned.

        Returns
        -------
        list[dict]
            Ordered list of model version dicts (most recent first).
        """
        if asset_class is not None:
            rows = self._conn.execute(
                """
                SELECT * FROM model_versions
                WHERE asset_class = ?
                ORDER BY deployed_at DESC
                """,
                (asset_class,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM model_versions ORDER BY deployed_at DESC"
            ).fetchall()

        return [_row_to_dict(r) for r in rows]

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Thin wrapper around ``Connection.execute`` — allows test injection."""
        return self._conn.execute(sql, params)

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()
