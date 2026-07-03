"""Shared test fixtures."""
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from parking_bot import dynamic_db  # noqa: E402


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
