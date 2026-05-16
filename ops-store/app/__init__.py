"""
ops-store/app -- storage service application package for OilOps-PredictiveCore.

Public surface
--------------
StorageInterface
    Abstract base class that every concrete storage backend must implement.
    Consuming services (ops-feature, ops-models, ops-explain, ops-api) depend
    only on this interface, not on the concrete DuckDB or SQLite backends
    (DA-02: pluggable storage without altering the core).
"""

from app.storage_interface import StorageInterface

__all__: list[str] = ["StorageInterface"]
