"""MCP server that persists CONFIRMED reservations to the secure text store.

Built on the official MCP Python SDK (``FastMCP``) and served over the
Streamable-HTTP transport, so it is a real, network-accessible MCP server that
the admin agent connects to as an MCP client.

Security
--------
The transport is wrapped with :class:`BearerAuthMiddleware`, which requires an
``Authorization: Bearer <MCP_AUTH_TOKEN>`` header on every request to the MCP
endpoint. The check is **fail-closed**: if no token is configured the server
rejects all requests, and the comparison is constant-time to avoid timing
leaks. Combined with the store's input sanitisation and fixed output path, this
protects against unauthorized access and file/format injection.

Run it with::

    export MCP_AUTH_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    uvicorn parking_bot.mcp_server:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import hmac
import logging
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp, Receive, Scope, Send

from . import reservation_store
from .config import settings

logger = logging.getLogger(__name__)

mcp = FastMCP("parking-reservation-store")


@mcp.tool()
def record_confirmed_reservation(
    name: str,
    car_number: str,
    reservation_period: str,
    approval_time: str = "",
) -> str:
    """Append a CONFIRMED reservation to the parking reservation store.

    Writes a single line ``Name | Car Number | Reservation Period | Approval
    Time`` to the secure text file. ``approval_time`` defaults to now (ISO
    seconds) if omitted. Field values are sanitised before writing.
    """
    if not approval_time:
        approval_time = datetime.now().isoformat(timespec="seconds")
    line = reservation_store.append_entry(
        reservation_store.ConfirmedReservation(
            name=name,
            car_number=car_number,
            reservation_period=reservation_period,
            approval_time=approval_time,
        )
    )
    logger.info("Recorded confirmed reservation: %s", line)
    return f"Recorded: {line}"


class BearerAuthMiddleware:
    """Fail-closed bearer-token gate for the MCP endpoint (pure ASGI)."""

    def __init__(self, app: ASGIApp, token: str, protected_prefix: str = "/mcp"):
        self.app = app
        self._token = token or ""
        self._prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith(self._prefix):
            if not self._authorized(scope):
                await self._reject(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        # Fail closed: no configured token => reject everything.
        if not self._token:
            return False
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {self._token}"
        return hmac.compare_digest(provided, expected)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = b"Unauthorized"
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def build_app(token: str | None = None) -> ASGIApp:
    """Return the Streamable-HTTP ASGI app wrapped in bearer-token auth."""
    if token is None:
        token = settings.mcp_auth_token
    if not token:
        logger.warning(
            "MCP_AUTH_TOKEN is not set — the MCP server is fail-closed and will "
            "reject ALL requests until a token is configured."
        )
    app = mcp.streamable_http_app()
    return BearerAuthMiddleware(app, token=token, protected_prefix=settings.mcp_path)


# ASGI entrypoint for `uvicorn parking_bot.mcp_server:app`.
app = build_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)
