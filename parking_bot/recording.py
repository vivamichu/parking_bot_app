"""Orchestrates recording of a confirmed reservation to the file store.

This is the single seam the admin agent calls after an approval. It builds the
``Name | Car Number | Reservation Period | Approval Time`` entry from a
reservation row and persists it:

* if ``MCP_ENABLED`` is true, it goes through the MCP server (the required
  integration);
* if the MCP call fails for any reason — or MCP is disabled — it falls back to a
  direct write to the very same file, so a confirmed reservation is **never
  silently lost** (reliability).
"""
from __future__ import annotations

import logging
from datetime import datetime

from . import mcp_client, reservation_store
from .config import settings

logger = logging.getLogger(__name__)


def build_entry(reservation: dict, approval_time: str | None = None) -> reservation_store.ConfirmedReservation:
    """Map a reservations-table row to a confirmed-reservation entry."""
    name = f"{reservation.get('first_name', '')} {reservation.get('last_name', '')}".strip()
    period = f"{reservation.get('start_time', '')} to {reservation.get('end_time', '')}".strip()
    return reservation_store.ConfirmedReservation(
        name=name,
        car_number=reservation.get("car_plate", ""),
        reservation_period=period,
        approval_time=approval_time or datetime.now().isoformat(timespec="seconds"),
    )


def record_confirmed(reservation: dict, approval_time: str | None = None) -> str:
    """Persist a confirmed reservation; returns the line that was written."""
    entry = build_entry(reservation, approval_time)
    if settings.mcp_enabled:
        try:
            result = mcp_client.record_via_mcp(entry)
            logger.info("Recorded confirmed reservation via MCP: %s", result)
            return result
        except Exception as exc:  # noqa: BLE001 - fall back so we never lose data
            logger.warning(
                "MCP recording failed (%s); falling back to direct file write.", exc
            )
    return reservation_store.append_entry(entry)
