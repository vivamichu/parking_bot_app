"""Reservation logic: validate the details collected from the user and persist a
*pending* reservation. Human confirmation (HITL) is handled by staff in a later
stage; here we only create the pending record.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from . import dynamic_db

# A license plate: 4-10 chars, letters/digits (optionally spaced/hyphenated).
_PLATE_RE = re.compile(r"^[A-Za-z0-9]{2,4}[- ]?[A-Za-z0-9]{2,6}$")
_NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ'\-\. ]{2,40}$")

_DATE_FORMATS = [
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
]


@dataclass
class ReservationDetails:
    first_name: str
    last_name: str
    car_plate: str
    start_time: str
    end_time: str
    space_type: str = "standard"


def _parse_dt(value: str) -> Optional[datetime]:
    value = value.strip()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def validate_name(name: str, field: str = "name") -> tuple[bool, str]:
    name = (name or "").strip()
    if not name:
        return False, f"Please provide your {field}."
    if not _NAME_RE.match(name):
        return False, f"That {field} doesn't look valid. Please use letters only."
    return True, name.title()


def validate_plate(plate: str) -> tuple[bool, str]:
    raw = (plate or "").strip().upper()
    if not raw:
        return False, "Please provide your car's license plate."
    if not _PLATE_RE.match(raw):
        return False, (
            "That license plate doesn't look valid. Use 4-10 letters/digits, "
            "e.g. 123ABC02."
        )
    return True, raw.replace(" ", "")


def validate_period(start: str, end: str) -> tuple[bool, str]:
    s, e = _parse_dt(start), _parse_dt(end)
    if s is None:
        return False, "I couldn't read the start date/time. Try e.g. 2026-07-10 09:00."
    if e is None:
        return False, "I couldn't read the end date/time. Try e.g. 2026-07-10 18:00."
    if e <= s:
        return False, "The end time must be after the start time."
    return True, "ok"


def validate_reservation(details: ReservationDetails) -> tuple[bool, list[str], ReservationDetails]:
    """Validate all fields. Returns (ok, errors, normalized_details)."""
    errors: list[str] = []
    ok_fn, first = validate_name(details.first_name, "first name")
    if not ok_fn:
        errors.append(first)
    ok_ln, last = validate_name(details.last_name, "last name")
    if not ok_ln:
        errors.append(last)
    ok_pl, plate = validate_plate(details.car_plate)
    if not ok_pl:
        errors.append(plate)
    ok_pe, msg = validate_period(details.start_time, details.end_time)
    if not ok_pe:
        errors.append(msg)

    space_type = (details.space_type or "standard").lower()
    if space_type not in dynamic_db.SPACE_TYPES:
        space_type = "standard"

    normalized = ReservationDetails(
        first_name=first if ok_fn else details.first_name,
        last_name=last if ok_ln else details.last_name,
        car_plate=plate if ok_pl else details.car_plate,
        start_time=details.start_time.strip(),
        end_time=details.end_time.strip(),
        space_type=space_type,
    )
    return (len(errors) == 0), errors, normalized


def create_pending_reservation(details: ReservationDetails, db_path: str | None = None) -> dict:
    """Validate and, if valid, persist a pending reservation.

    Returns a dict with either ``{"ok": True, "reservation_id": id, ...}`` or
    ``{"ok": False, "errors": [...]}``.
    """
    ok, errors, norm = validate_reservation(details)
    if not ok:
        return {"ok": False, "errors": errors}

    # Confirm a space of the requested type is actually free.
    avail = dynamic_db.get_availability(space_type=norm.space_type, db_path=db_path)
    free = sum(row["free"] for row in avail)
    if free <= 0:
        return {
            "ok": False,
            "errors": [f"Sorry, no {norm.space_type} spaces are currently free."],
        }

    res_id = dynamic_db.insert_reservation(
        first_name=norm.first_name,
        last_name=norm.last_name,
        car_plate=norm.car_plate,
        start_time=norm.start_time,
        end_time=norm.end_time,
        space_type=norm.space_type,
        db_path=db_path,
    )
    return {
        "ok": True,
        "reservation_id": res_id,
        "status": "pending",
        "details": asdict(norm),
        "message": (
            f"Reservation #{res_id} created and is now PENDING confirmation by an "
            f"administrator. We reserved a {norm.space_type} space for "
            f"{norm.first_name} {norm.last_name} (plate {norm.car_plate}) from "
            f"{norm.start_time} to {norm.end_time}."
        ),
    }
