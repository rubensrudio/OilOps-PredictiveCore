"""
ops-store/app -- storage service application package for OilOps-PredictiveCore.

Public surface
--------------
StorageInterface
    Abstract base class that every concrete storage backend must implement.
    Consuming services (ops-feature, ops-models, ops-explain, ops-api) depend
    only on this interface, not on the concrete DuckDB or SQLite backends
    (DA-02: pluggable storage without altering the core).

Import note
-----------
This __init__.py deliberately does NOT import StorageInterface at module level
to avoid registering sys.modules["app"] when the service root (ops-store/) is
on sys.path.  Consumers should always import via the fully qualified alias::

    from ops_store.app.storage_interface import StorageInterface
"""
