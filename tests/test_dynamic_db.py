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
