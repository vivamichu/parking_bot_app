"""Minimal MCP client used by the admin agent to record confirmed reservations.

Connects to the reservation-store MCP server over Streamable-HTTP, authenticates
with the bearer token, and calls the ``record_confirmed_reservation`` tool.

The async client is wrapped in :func:`record_via_mcp`, a synchronous helper that
works whether or not the caller already runs inside an asyncio event loop (the
LangChain tool that calls it may run in a FastAPI thread-pool, in ``run_turn``,
or under pytest).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from . import reservation_store
from .config import settings


def _extract_text(result: Any) -> str:
    """Pull a text payload out of an MCP ``CallToolResult``."""
    content = getattr(result, "content", None) or []
    parts = [getattr(c, "text", "") for c in content if getattr(c, "text", "")]
    return " ".join(parts).strip() or "recorded"


async def _record_async(rec: reservation_store.ConfirmedReservation, url: str, token: str) -> str:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "record_confirmed_reservation",
                {
                    "name": rec.name,
                    "car_number": rec.car_number,
                    "reservation_period": rec.reservation_period,
                    "approval_time": rec.approval_time,
                },
            )
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP tool returned an error: {_extract_text(result)}")
    return _extract_text(result)


def _run_async(coro) -> Any:
    """Run ``coro`` to completion regardless of the caller's loop state."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is None:
        return asyncio.run(coro)

    # Already inside an event loop: run in a dedicated thread with its own loop.
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


def record_via_mcp(rec: reservation_store.ConfirmedReservation) -> str:
    """Record one confirmed reservation through the MCP server (synchronous)."""
    return _run_async(_record_async(rec, settings.mcp_url, settings.mcp_auth_token))
