"""Fast load / concurrency tests for the pipeline's components.

These run in CI as a scaled-down load check: they hammer each write path
concurrently and assert correctness under contention (no lost, corrupted, or
duplicated records). The standalone ``scripts/load_test.py`` reports throughput
numbers for a larger run.
"""
import concurrent.futures as cf

from parking_bot import dynamic_db, orchestrator, reservation_store
from parking_bot.reservation_store import ConfirmedReservation


def test_recording_is_lossless_under_concurrency(store_path):
    """The MCP store's lock must serialise concurrent appends without corruption."""
    n = 300
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        list(
            ex.map(
                lambda i: reservation_store.append_entry(
                    ConfirmedReservation(f"User {i}", f"PLATE{i:04d}", "P", "T")
                ),
                range(n),
            )
        )
    lines = reservation_store.read_entries(store_path)
    assert len(lines) == n  # no lost writes
    assert all(ln.count(" | ") == 3 for ln in lines)  # no interleaving/corruption
    assert len(set(lines)) == n  # all distinct


def test_db_reservation_inserts_under_concurrency(temp_db):
    """Concurrent reservation writes (chatbot intake) all succeed with busy_timeout."""
    n = 200

    def _insert(i):
        return dynamic_db.insert_reservation(
            "A", "B", f"PL{i:04d}",
            "2026-07-10 09:00", "2026-07-10 18:00", "standard",
            session_id=f"s{i}", db_path=temp_db,
        )

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        ids = list(ex.map(_insert, range(n)))

    assert len(set(ids)) == n  # unique ids, no lost inserts
    assert len(dynamic_db.list_pending_reservations(db_path=temp_db)) == n


def test_pipeline_throughput_many_bookings(pipeline_env):
    """Run many full approve pipelines back-to-back; every one records exactly once."""
    n = 25
    for i in range(n):
        g = orchestrator.build_graph()  # fresh checkpointer per booking
        done = orchestrator.run_full(
            {
                # Names must be letters only; the unique index goes in the plate.
                "first_name": "Load",
                "last_name": "Tester",
                "car_plate": f"LT{i:04d}A",
                "start_time": "2026-07-10 09:00",
                "end_time": "2026-07-10 18:00",
                "space_type": "standard",
            },
            "approve",
            session_id=f"load-{i}",
            graph=g,
        )
        assert done["status"] == "confirmed"

    assert len(reservation_store.read_entries(pipeline_env["store"])) == n
