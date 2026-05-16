"""
ops_store/app — importable alias for ops-store/app.

Re-exports ``StorageInterface`` from the canonical location at
``ops-store/app/storage_interface.py`` WITHOUT adding the service root to
sys.path or touching sys.modules["app"].

Importing this package triggers ``ops_store/app/storage_interface.py`` which
loads the implementation via ``importlib.util.spec_from_file_location``,
keeping sys.modules["app"] free for whichever service is under test.

This makes the following import work from the project root::

    from ops_store.app.storage_interface import StorageInterface
"""

from __future__ import annotations

# Re-export via the sibling alias module which handles sys.modules registration
# without touching sys.modules["app"].
from ops_store.app.storage_interface import StorageInterface  # noqa: F401

__all__: list[str] = ["StorageInterface"]
