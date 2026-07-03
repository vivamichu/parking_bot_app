"""Tests for the secure confirmed-reservation file store (Stage 3)."""
import pytest

from parking_bot import reservation_store as rs
from parking_bot.reservation_store import ConfirmedReservation, ReservationStoreError


def test_format_entry_uses_pipe_layout():
    line = rs.format_entry(
        ConfirmedReservation(
            "Aya Rai", "342GHB01", "2026-07-03 to 2026-07-20", "2026-07-03T09:00:00"
        )
    )
    assert line == "Aya Rai | 342GHB01 | 2026-07-03 to 2026-07-20 | 2026-07-03T09:00:00"
    assert line.count(" | ") == 3  # exactly four columns


def test_append_and_read_roundtrip(store_path):
    rs.append_entry(ConfirmedReservation("Aya Rai", "342GHB01", "P", "T"))
    rs.append_entry(ConfirmedReservation("Bob Lee", "77ABC01", "P2", "T2"))
    lines = rs.read_entries()
    assert len(lines) == 2
    assert lines[0].startswith("Aya Rai | 342GHB01 |")
    assert lines[1].startswith("Bob Lee | 77ABC01 |")


def test_pipe_and_newline_injection_is_neutralised(store_path):
    """A crafted field must not forge extra columns or inject extra lines."""
    rs.append_entry(
        ConfirmedReservation(
            "Evil|Name\nInjected 999", "P|LATE", "per\niod", "when"
        )
    )
    lines = rs.read_entries()
    assert len(lines) == 1  # the embedded newline did NOT create a second line
    assert lines[0].count(" | ") == 3  # still exactly four columns
    # No stray delimiter survives inside a field value.
    assert "|" not in lines[0].replace(" | ", "")


def test_empty_required_field_rejected():
    with pytest.raises(ReservationStoreError):
        rs.format_entry(ConfirmedReservation("", "PLATE", "P", "T"))


def test_overlong_field_rejected():
    with pytest.raises(ReservationStoreError):
        rs.format_entry(ConfirmedReservation("x" * 201, "PLATE", "P", "T"))


def test_read_entries_missing_file_returns_empty(tmp_path):
    assert rs.read_entries(str(tmp_path / "does_not_exist.txt")) == []
