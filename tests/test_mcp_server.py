"""Tests for the MCP reservation-store server: auth gate + tool behaviour."""
import asyncio

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from parking_bot import mcp_server, reservation_store
from parking_bot.mcp_server import BearerAuthMiddleware


def _client(token):
    async def ok(request):  # inner app that should only be reached when authorized
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", ok), Route("/health", ok)])
    app.add_middleware(BearerAuthMiddleware, token=token, protected_prefix="/mcp")
    return TestClient(app)


def test_auth_is_fail_closed_without_token():
    # No token configured => every protected request is rejected.
    assert _client("").get("/mcp").status_code == 401


def test_auth_rejects_wrong_token():
    resp = _client("secret").get("/mcp", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_auth_accepts_correct_token():
    resp = _client("secret").get("/mcp", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_unprotected_path_is_not_gated():
    # Paths outside the MCP prefix are untouched by the auth middleware.
    assert _client("secret").get("/health").status_code == 200


def test_record_tool_is_registered():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert any(t.name == "record_confirmed_reservation" for t in tools)


def test_record_tool_writes_formatted_entry(store_path):
    out = mcp_server.record_confirmed_reservation(
        "Aya Rai", "342GHB01", "2026-07-03 to 2026-07-20", "2026-07-03T09:00:00"
    )
    assert "Recorded:" in out
    line = reservation_store.read_entries()[-1]
    assert line == "Aya Rai | 342GHB01 | 2026-07-03 to 2026-07-20 | 2026-07-03T09:00:00"
