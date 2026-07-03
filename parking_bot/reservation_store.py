"""Secure, append-only text store for CONFIRMED reservations.

Each confirmed reservation is written as a single pipe-delimited line:

    Name | Car Number | Reservation Period | Approval Time

Security & reliability properties:

* **Injection-safe** — ``|`` and any newline/control characters are stripped
  from field values, so a crafted name or plate cannot forge extra columns or
  inject additional lines into the file.
* **No path traversal** — the destination is a fixed, config-controlled path;
  callers never supply a path in normal operation.
* **Concurrency-safe** — a process-level lock plus append-mode writes make
  concurrent recordings (MCP server + direct fallback) safe; each ``append``
  writes exactly one ``\\n``-terminated line.
* **Validated** — empty required fields and over-long values are rejected.

This module is deliberately free of any MCP / network code so it can be unit
tested in isolation and reused both by the MCP server and the direct-write
fallback.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import settings

_WRITE_LOCK = threading.Lock()

# Reject absurdly long values (defence against unbounded/abusive input).
_MAX_FIELD_LEN = 200
# Characters we never allow inside a field: the delimiter and any control char.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ReservationStoreError(ValueError):
    """Raised when a confirmed-reservation entry is invalid."""


@dataclass(frozen=True)
class ConfirmedReservation:
    name: str
    car_number: str
    reservation_period: str
    approval_time: str


def _sanitize(value: str, field: str, *, required: bool = True) -> str:
    """Make a single field safe for a pipe-delimited, line-based file."""
    if value is None:
        value = ""
    # Collapse whitespace, drop control chars, neutralise the delimiter.
    cleaned = _CONTROL_RE.sub(" ", str(value))
    cleaned = cleaned.replace("|", "/").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if required and not cleaned:
        raise ReservationStoreError(f"'{field}' must not be empty.")
    if len(cleaned) > _MAX_FIELD_LEN:
        raise ReservationStoreError(
            f"'{field}' exceeds {_MAX_FIELD_LEN} characters."
        )
    return cleaned


def format_entry(rec: ConfirmedReservation) -> str:
    """Return the sanitized one-line representation (no trailing newline)."""
    return " | ".join(
        (
            _sanitize(rec.name, "name"),
            _sanitize(rec.car_number, "car_number"),
            _sanitize(rec.reservation_period, "reservation_period"),
            _sanitize(rec.approval_time, "approval_time"),
        )
    )


def append_entry(rec: ConfirmedReservation, path: Optional[str] = None) -> str:
    """Validate, format, and atomically append one entry. Returns the line."""
    line = format_entry(rec)
    target = Path(path or settings.confirmed_reservations_path)
    with _WRITE_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return line


def read_entries(path: Optional[str] = None) -> list[str]:
    """Return all recorded lines (utility for tests / inspection)."""
    target = Path(path or settings.confirmed_reservations_path)
    if not target.exists():
        return []
    return [ln for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()]
