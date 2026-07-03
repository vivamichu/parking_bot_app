"""Dynamic data store (SQLite): live availability, working hours, pricing, and
reservations. This data changes over time, so it lives in a relational DB rather
than the vector store.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .config import settings

# Space types available in the facility.
SPACE_TYPES = ["standard", "compact", "premium", "ev", "accessible"]

# Deterministic seed data (no randomness so results are reproducible in tests).
# (level, space_type, count)
_LAYOUT = [
    (1, "standard", 70), (1, "ev", 20), (1, "accessible", 10),
    (2, "standard", 80), (2, "compact", 30),
    (3, "standard", 60), (3, "accessible", 10),
    (4, "standard", 90),
    (-1, "premium", 40), (-1, "accessible", 10),
]  # total = 420

_PRICING = [
    # space_type, hourly, daily, monthly, currency
    ("standard", 400, 3000, 45000, "KZT"),
    ("compact", 300, 2200, 35000, "KZT"),
    ("premium", 700, 5000, 70000, "KZT"),
    ("ev", 500, 3500, 50000, "KZT"),
    ("accessible", 0, 0, 0, "KZT"),
]

_HOURS = [
    ("Parking gates (entry/exit)", "Mon-Sun", "00:00", "24:00"),
    ("Information desk", "Mon-Sun", "08:00", "20:00"),
    ("Car wash", "Mon-Sat", "09:00", "18:00"),
]


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or settings.sqlite_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait (rather than immediately error) if another connection holds the write
    # lock — keeps the pipeline stable under concurrent load.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: Optional[str] = None, reset: bool = False) -> None:
    """Create tables and seed reference data. Idempotent unless reset=True."""
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        if reset:
            for t in ("parking_spots", "pricing", "working_hours", "reservations", "notifications"):
                cur.execute(f"DROP TABLE IF EXISTS {t}")

        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS parking_spots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                level      INTEGER NOT NULL,
                space_type TEXT    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'free'
                           CHECK (status IN ('free', 'occupied', 'reserved'))
            );
            CREATE TABLE IF NOT EXISTS pricing (
                space_type   TEXT PRIMARY KEY,
                hourly_rate  INTEGER NOT NULL,
                daily_rate   INTEGER NOT NULL,
                monthly_rate INTEGER NOT NULL,
                currency     TEXT NOT NULL DEFAULT 'KZT'
            );
            CREATE TABLE IF NOT EXISTS working_hours (
                service   TEXT PRIMARY KEY,
                days      TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reservations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                first_name   TEXT NOT NULL,
                last_name    TEXT NOT NULL,
                car_plate    TEXT NOT NULL,
                start_time   TEXT NOT NULL,
                end_time     TEXT NOT NULL,
                space_type   TEXT NOT NULL DEFAULT 'standard',
                status       TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'confirmed', 'cancelled')),
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                reservation_id INTEGER NOT NULL,
                message      TEXT NOT NULL,
                seen         INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )

        # Seed reference tables only if empty.
        if cur.execute("SELECT COUNT(*) FROM parking_spots").fetchone()[0] == 0:
            for level, stype, count in _LAYOUT:
                for i in range(count):
                    # Deterministic occupancy: ~1 in 3 occupied, 1 in 10 reserved.
                    if i % 10 == 0:
                        status = "reserved"
                    elif i % 3 == 0:
                        status = "occupied"
                    else:
                        status = "free"
                    cur.execute(
                        "INSERT INTO parking_spots (level, space_type, status) VALUES (?,?,?)",
                        (level, stype, status),
                    )
        if cur.execute("SELECT COUNT(*) FROM pricing").fetchone()[0] == 0:
            cur.executemany(
                "INSERT INTO pricing VALUES (?,?,?,?,?)", _PRICING
            )
        if cur.execute("SELECT COUNT(*) FROM working_hours").fetchone()[0] == 0:
            cur.executemany(
                "INSERT INTO working_hours VALUES (?,?,?,?)", _HOURS
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Query functions (used by agent tools)
# --------------------------------------------------------------------------- #
def get_availability(
    space_type: Optional[str] = None,
    level: Optional[int] = None,
    db_path: Optional[str] = None,
) -> list[dict]:
    """Return free-space counts grouped by level and type, optionally filtered."""
    conn = get_connection(db_path)
    try:
        q = (
            "SELECT level, space_type, "
            "SUM(status='free') AS free, COUNT(*) AS total "
            "FROM parking_spots"
        )
        conds, params = [], []
        if space_type:
            conds.append("space_type = ?")
            params.append(space_type.lower())
        if level is not None:
            conds.append("level = ?")
            params.append(level)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " GROUP BY level, space_type ORDER BY level, space_type"
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_total_free(db_path: Optional[str] = None) -> int:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM parking_spots WHERE status='free'"
        ).fetchone()[0]
    finally:
        conn.close()


def get_pricing(
    space_type: Optional[str] = None, db_path: Optional[str] = None
) -> list[dict]:
    conn = get_connection(db_path)
    try:
        if space_type:
            rows = conn.execute(
                "SELECT * FROM pricing WHERE space_type = ?", (space_type.lower(),)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pricing").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_working_hours(db_path: Optional[str] = None) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM working_hours").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def insert_reservation(
    first_name: str,
    last_name: str,
    car_plate: str,
    start_time: str,
    end_time: str,
    space_type: str = "standard",
    session_id: str = "",
    db_path: Optional[str] = None,
) -> int:
    """Insert a pending reservation and return its id."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO reservations "
            "(session_id, first_name, last_name, car_plate, start_time, end_time, space_type, status) "
            "VALUES (?,?,?,?,?,?,?, 'pending')",
            (session_id, first_name, last_name, car_plate, start_time, end_time, space_type),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_reservation(res_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM reservations WHERE id = ?", (res_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def list_pending_reservations(db_path: Optional[str] = None) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM reservations WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def set_reservation_status(res_id:int, status:str, db_path: Optional[str]=None) -> None:
    """Update the status of a reservation."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE reservations SET status = ? WHERE id = ?", (status, res_id)
        )
        conn.commit()
    finally:
        conn.close()

def add_notification(session_id: str, reservation_id: int, message: str, db_path: Optional[str] = None) -> None:
    """Add a notification for a reservation and return its id."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO notifications (session_id, reservation_id, message) "
            "VALUES (?,?,?)",
            (session_id, reservation_id, message),
        )
        conn.commit()

    finally:
        conn.close()

def get_unseen_notifications(session_id: str, reservation_id: Optional[int]=None, message: Optional[str]=None, db_path: Optional[str] = None) -> list[dict]:
    """Retrieve unseen notifications for a session, optionally filtered by reservation_id and message."""
    conn = get_connection(db_path)
    try:
        q = "SELECT * FROM notifications WHERE session_id = ? AND seen = 0"
        params = [session_id]
        if reservation_id is not None:
            q += " AND reservation_id = ?"
            params.append(reservation_id)
        if message is not None:
            q += " AND message LIKE ?"
            params.append(f"%{message}%")
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def mark_notifications_seen(notification_ids: list[int], db_path: Optional[str] = None) -> None:
    """Mark notifications as seen."""
    if not notification_ids:
        return
    conn = get_connection(db_path)
    try:
        q = f"UPDATE notifications SET seen = 1 WHERE id IN ({','.join(['?']*len(notification_ids))})"
        conn.execute(q, notification_ids)
        conn.commit()
    finally:
        conn.close()

if __name__ == "__main__":
    init_db(reset=True)
    print("Seeded SQLite at", settings.sqlite_path)
    print("Total free spaces:", get_total_free())
    print("Pricing rows:", len(get_pricing()))
