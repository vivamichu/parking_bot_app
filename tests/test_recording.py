"""Tests for the recording orchestrator (MCP path + reliable fallback)."""
import types

from parking_bot import recording, reservation_store

RES = {
    "first_name": "Aya",
    "last_name": "Rai",
    "car_plate": "342GHB01",
    "start_time": "2026-07-03 00:00",
    "end_time": "2026-07-20 00:00",
    "status": "confirmed",
    "session_id": "sess-1",
}


def test_build_entry_maps_reservation_row():
    e = recording.build_entry(RES, approval_time="2026-07-03T09:00:00")
    assert e.name == "Aya Rai"
    assert e.car_number == "342GHB01"
    assert e.reservation_period == "2026-07-03 00:00 to 2026-07-20 00:00"
    assert e.approval_time == "2026-07-03T09:00:00"


def test_direct_write_when_mcp_disabled(store_path, monkeypatch):
    monkeypatch.setattr(recording, "settings", types.SimpleNamespace(mcp_enabled=False))
    line = recording.record_confirmed(RES, approval_time="T")
    assert reservation_store.read_entries()[-1] == line
    assert line.startswith("Aya Rai | 342GHB01 |")


def test_uses_mcp_when_enabled(store_path, monkeypatch):
    monkeypatch.setattr(recording, "settings", types.SimpleNamespace(mcp_enabled=True))
    captured = {}

    def _fake(entry):
        captured["entry"] = entry
        return "Recorded via MCP"

    monkeypatch.setattr(recording.mcp_client, "record_via_mcp", _fake)
    out = recording.record_confirmed(RES, approval_time="T")
    assert out == "Recorded via MCP"
    assert captured["entry"].name == "Aya Rai"
    # MCP path taken → no direct local write happened.
    assert reservation_store.read_entries() == []


def test_falls_back_to_direct_write_when_mcp_fails(store_path, monkeypatch):
    monkeypatch.setattr(recording, "settings", types.SimpleNamespace(mcp_enabled=True))

    def _boom(entry):
        raise RuntimeError("MCP server unreachable")

    monkeypatch.setattr(recording.mcp_client, "record_via_mcp", _boom)
    line = recording.record_confirmed(RES, approval_time="T")
    # The reservation is still persisted despite the MCP failure.
    assert reservation_store.read_entries()[-1] == line
