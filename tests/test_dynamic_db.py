from parking_bot import dynamic_db


def test_seed_creates_420_spaces_and_pricing(temp_db):
    avail = dynamic_db.get_availability(db_path=temp_db)
    total = sum(r["total"] for r in avail)
    assert total == 420
    pricing = dynamic_db.get_pricing(db_path=temp_db)
    types = {r["space_type"] for r in pricing}
    assert {"standard", "compact", "premium", "ev", "accessible"} <= types


def test_availability_filter_by_type(temp_db):
    ev = dynamic_db.get_availability(space_type="ev", db_path=temp_db)
    assert ev, "expected EV rows"
    assert all(r["space_type"] == "ev" for r in ev)
    assert dynamic_db.get_total_free(db_path=temp_db) > 0


def test_working_hours_present(temp_db):
    hours = dynamic_db.get_working_hours(db_path=temp_db)
    services = {h["service"] for h in hours}
    assert any("gate" in s.lower() for s in services)


def test_insert_and_read_reservation(temp_db):
    res_id = dynamic_db.insert_reservation(
        "Al-Farabi", "Testov", "123ABC02",
        "2026-07-10 09:00", "2026-07-10 18:00", "standard", db_path=temp_db,
    )
    row = dynamic_db.get_reservation(res_id, db_path=temp_db)
    assert row["status"] == "pending"
    assert row["car_plate"] == "123ABC02"


# --------------------------------------------------------------------------- #
# Stage 2: session id, pending queue, status transitions, notification outbox
# --------------------------------------------------------------------------- #
def _seed_pending(temp_db, session_id="sess-1"):
    return dynamic_db.insert_reservation(
        "Aya", "Rai", "342GHB01",
        "2026-07-03 00:00", "2026-07-20 00:00", "standard",
        session_id=session_id, db_path=temp_db,
    )


def test_session_id_persisted_on_insert(temp_db):
    rid = _seed_pending(temp_db, session_id="sess-42")
    assert dynamic_db.get_reservation(rid, db_path=temp_db)["session_id"] == "sess-42"


def test_list_pending_then_status_transition_removes_from_queue(temp_db):
    rid = _seed_pending(temp_db)
    pending = dynamic_db.list_pending_reservations(db_path=temp_db)
    assert any(r["id"] == rid for r in pending)

    dynamic_db.set_reservation_status(rid, "confirmed", db_path=temp_db)
    assert dynamic_db.get_reservation(rid, db_path=temp_db)["status"] == "confirmed"
    assert all(
        r["id"] != rid for r in dynamic_db.list_pending_reservations(db_path=temp_db)
    )


def test_notification_outbox_roundtrip(temp_db):
    dynamic_db.add_notification(
        "sess-1", 42, "Your reservation #42 has been CONFIRMED.", db_path=temp_db
    )
    unseen = dynamic_db.get_unseen_notifications("sess-1", db_path=temp_db)
    assert len(unseen) == 1
    assert unseen[0]["reservation_id"] == 42

    dynamic_db.mark_notifications_seen([unseen[0]["id"]], db_path=temp_db)
    assert dynamic_db.get_unseen_notifications("sess-1", db_path=temp_db) == []


def test_notifications_scoped_to_session(temp_db):
    dynamic_db.add_notification("sess-a", 1, "A", db_path=temp_db)
    dynamic_db.add_notification("sess-b", 2, "B", db_path=temp_db)
    assert len(dynamic_db.get_unseen_notifications("sess-a", db_path=temp_db)) == 1
    assert len(dynamic_db.get_unseen_notifications("sess-b", db_path=temp_db)) == 1
