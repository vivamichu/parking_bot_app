"""End-to-end integration test: a live MCP server + the real MCP client.

Starts the Streamable-HTTP MCP server (bearer-token protected) once in a
background uvicorn thread, then:
  * records a reservation through ``mcp_client.record_via_mcp`` and asserts the
    file was written in the required format;
  * asserts a wrong token is rejected.

A single server is shared by both tests because the FastMCP session manager may
only be run once per process.
"""
import socket
import threading
import time
import types

import pytest

from parking_bot import mcp_client, reservation_store
from parking_bot.reservation_store import ConfirmedReservation

TOKEN = "integration-test-token"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_mcp(tmp_path_factory):
    import uvicorn

    from parking_bot import mcp_server

    store = str(tmp_path_factory.mktemp("store") / "confirmed.txt")
    original = reservation_store.settings
    reservation_store.settings = types.SimpleNamespace(
        confirmed_reservations_path=store
    )

    port = _free_port()
    app = mcp_server.build_app(token=TOKEN)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)

    try:
        if not server.started:
            pytest.skip("MCP server did not start in time")
        yield {"url": f"http://127.0.0.1:{port}/mcp", "store": store}
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        reservation_store.settings = original


def _point_client(url, token):
    mcp_client.settings = types.SimpleNamespace(mcp_url=url, mcp_auth_token=token)


def test_record_via_mcp_roundtrip(live_mcp):
    original = mcp_client.settings
    _point_client(live_mcp["url"], TOKEN)
    try:
        out = mcp_client.record_via_mcp(
            ConfirmedReservation(
                "Aya Rai", "342GHB01", "2026-07-03 to 2026-07-20", "2026-07-03T09:00:00"
            )
        )
    finally:
        mcp_client.settings = original

    assert "Recorded" in out
    lines = reservation_store.read_entries(live_mcp["store"])
    assert lines[-1] == "Aya Rai | 342GHB01 | 2026-07-03 to 2026-07-20 | 2026-07-03T09:00:00"


def test_record_via_mcp_rejected_with_bad_token(live_mcp):
    original = mcp_client.settings
    _point_client(live_mcp["url"], "wrong-token")
    try:
        with pytest.raises(Exception):
            mcp_client.record_via_mcp(
                ConfirmedReservation("X Y", "PLATE01", "P", "T")
            )
    finally:
        mcp_client.settings = original
