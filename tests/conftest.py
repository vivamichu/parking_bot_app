"""Shared test fixtures."""
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from parking_bot import dynamic_db  # noqa: E402


@pytest.fixture(autouse=True)
def _mcp_disabled_by_default(monkeypatch):
    """Keep the suite hermetic: never touch a live MCP server from a developer's
    local ``.env`` (MCP_ENABLED=true). Tests that exercise MCP set their own
    settings and override this. In CI there is no ``.env`` so MCP is off anyway.
    """
    import types

    from parking_bot import recording

    monkeypatch.setattr(recording, "settings", types.SimpleNamespace(mcp_enabled=False))


@pytest.fixture()
def temp_db(tmp_path):
    """A freshly seeded, isolated SQLite database path."""
    db_path = str(tmp_path / "test_parking.db")
    dynamic_db.init_db(db_path=db_path, reset=True)
    return db_path


@pytest.fixture()
def default_db(tmp_path, monkeypatch):
    """Point the *module-default* DB at an isolated seeded database.

    The admin agent and REST server call ``dynamic_db`` functions without an
    explicit ``db_path`` (they use ``settings.sqlite_path``). Repointing
    ``dynamic_db.settings`` lets those code paths run against a throwaway DB.
    """
    import types

    db_path = str(tmp_path / "default_parking.db")
    dynamic_db.init_db(db_path=db_path, reset=True)
    monkeypatch.setattr(
        dynamic_db, "settings", types.SimpleNamespace(sqlite_path=db_path)
    )
    return db_path


@pytest.fixture()
def store_path(tmp_path, monkeypatch):
    """Point the confirmed-reservation store at an isolated temp file.

    ``reservation_store.append_entry`` uses ``settings.confirmed_reservations_path``
    when no explicit path is given; repointing ``reservation_store.settings``
    keeps Stage-3 recording tests hermetic.
    """
    import types

    from parking_bot import reservation_store

    path = str(tmp_path / "confirmed.txt")
    monkeypatch.setattr(
        reservation_store,
        "settings",
        types.SimpleNamespace(confirmed_reservations_path=path),
    )
    return path


@pytest.fixture()
def pipeline_env(default_db, store_path, monkeypatch):
    """Isolate the whole Stage-4 pipeline: seeded DB, temp store, MCP disabled.

    With ``mcp_enabled=False`` the recording node writes directly to the temp
    store, so orchestrator tests are deterministic and need no live MCP server.
    """
    import types

    from parking_bot import recording

    monkeypatch.setattr(recording, "settings", types.SimpleNamespace(mcp_enabled=False))
    return {"db": default_db, "store": store_path}
