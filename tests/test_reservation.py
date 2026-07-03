from parking_bot import reservation
from parking_bot.reservation import ReservationDetails


def test_validate_plate_accepts_and_normalizes():
    ok, val = reservation.validate_plate("123 abc02")
    assert ok
    assert val == "123ABC02"


def test_validate_plate_rejects_garbage():
    ok, msg = reservation.validate_plate("!!")
    assert not ok


def test_validate_period_rejects_end_before_start():
    ok, msg = reservation.validate_period("2026-07-10 18:00", "2026-07-10 09:00")
    assert not ok


def test_validate_reservation_collects_all_errors():
    ok, errors, _ = reservation.validate_reservation(
        ReservationDetails("", "", "??", "bad", "worse")
    )
    assert not ok
    assert len(errors) >= 3


def test_create_pending_reservation_persists(temp_db):
    result = reservation.create_pending_reservation(
        ReservationDetails(
            "Aida", "Nurlan", "777xyz01",
            "2026-07-10 09:00", "2026-07-10 18:00", "standard",
        ),
        db_path=temp_db,
    )
    assert result["ok"] is True
    assert result["status"] == "pending"
    assert isinstance(result["reservation_id"], int)


def test_create_pending_reservation_rejects_invalid(temp_db):
    result = reservation.create_pending_reservation(
        ReservationDetails("A", "", "x", "bad", "bad"), db_path=temp_db
    )
    assert result["ok"] is False
    assert result["errors"]
