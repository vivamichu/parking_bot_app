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
