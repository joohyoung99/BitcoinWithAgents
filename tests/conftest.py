import sys
from unittest.mock import MagicMock

import pytest

# Mock bitpy.rest_api before importing src.executor
sys.modules["bitpy.rest_api"] = MagicMock()
sys.modules["bitpy"] = MagicMock()

# Mock BitgetAPI
mock_bitget = MagicMock()
sys.modules["bitpy.rest_api"].BitgetAPI = MagicMock(return_value=mock_bitget)


# --- shared DB testing helpers ---

class _FakeCursor:
    def __init__(self, store: list):
        self._store = store

    def execute(self, sql: str, params=None) -> None:
        self._store.append((sql, params or ()))

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, store: list):
        self._cur = _FakeCursor(store)

    def cursor(self) -> _FakeCursor:
        return self._cur

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def patch_db(monkeypatch):
    """Patches src.db._connect with a fake and returns the list of (sql, params) executed."""
    import src.db as db_mod
    store: list = []
    monkeypatch.setattr(db_mod, "_connect", lambda: _FakeConn(store))
    return store
