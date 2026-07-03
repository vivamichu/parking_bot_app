"""Load test for the parking pipeline — reports throughput per component.

Measures the three components the task calls out:
  1. Chatbot intake      — creating reservations (SQLite write path)
  2. Admin + pipeline     — full orchestrated approve (intake→approval→record)
  3. MCP recording        — the confirmed-reservation store (a) direct appends
                            under concurrency and (b) a LIVE MCP server round-trip

Everything runs against isolated temp storage, so it is safe to run anytime.

Usage:
    python scripts/load_test.py                 # defaults
    python scripts/load_test.py -n 2000 -w 32   # heavier
    python scripts/load_test.py --skip-mcp      # skip the live-server section
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import socket
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from parking_bot import (  # noqa: E402
    dynamic_db,
    mcp_client,
    orchestrator,
    recording,
    reservation_store,
)
from parking_bot.reservation_store import ConfirmedReservation  # noqa: E402


def _isolate(tmp: str):
    db = os.path.join(tmp, "load.db")
    store = os.path.join(tmp, "load.txt")
    dynamic_db.init_db(db_path=db, reset=True)
    dynamic_db.settings = types.SimpleNamespace(sqlite_path=db)
    reservation_store.settings = types.SimpleNamespace(confirmed_reservations_path=store)
    recording.settings = types.SimpleNamespace(mcp_enabled=False)
    return db, store


def _bench(label: str, n: int, workers: int, fn) -> None:
    errors = 0
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fn, i) for i in range(n)]
        for f in cf.as_completed(futures):
            try:
                f.result()
            except Exception:  # noqa: BLE001
                errors += 1
    dt = time.perf_counter() - t0
    ops = n / dt if dt else 0.0
    print(
        f"  {label:42} n={n:5d} w={workers:>3}  "
        f"{dt * 1000:8.1f} ms  {ops:9.1f} ops/s  "
        f"{dt / n * 1000:6.2f} ms/op  errors={errors}"
    )


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def bench_intake(n: int, workers: int) -> None:
    def _fn(i: int) -> None:
        dynamic_db.insert_reservation(
            "Load", f"User{i}", f"PL{i:05d}A",
            "2026-07-10 09:00", "2026-07-10 18:00", "standard",
            session_id=f"s{i}",
        )

    _bench("1. Chatbot intake (reservation create)", n, workers, _fn)


def bench_full_pipeline(n: int, workers: int) -> None:
    def _fn(i: int) -> None:
        g = orchestrator.build_graph()
        done = orchestrator.run_full(
            {
                # Names are letters only (validated); index goes in the plate.
                "first_name": "Load", "last_name": "Tester",
                "car_plate": f"FP{i:05d}A",
                "start_time": "2026-07-10 09:00", "end_time": "2026-07-10 18:00",
                "space_type": "standard",
            },
            "approve",
            session_id=f"fp{i}",
            graph=g,
        )
        if done["status"] != "confirmed":
            raise RuntimeError(done.get("outcome"))

    _bench("2. Admin approval + record (full graph)", n, workers, _fn)


def bench_recording_direct(n: int, workers: int) -> None:
    def _fn(i: int) -> None:
        reservation_store.append_entry(
            ConfirmedReservation(f"User {i}", f"PLATE{i:05d}", "period", "t")
        )

    _bench("3a. MCP store append (direct, locked)", n, workers, _fn)


def bench_mcp_server(n: int, workers: int) -> None:
    import uvicorn

    from parking_bot import mcp_server

    token = "loadtest-token"
    port = _free_port()
    app = mcp_server.build_app(token=token)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        print("  3b. MCP server round-trip: SKIPPED (server did not start)")
        return

    mcp_client.settings = types.SimpleNamespace(
        mcp_url=f"http://127.0.0.1:{port}/mcp", mcp_auth_token=token
    )

    def _fn(i: int) -> None:
        mcp_client.record_via_mcp(
            ConfirmedReservation(f"User {i}", f"MCP{i:05d}", "period", "t")
        )

    try:
        # Lower concurrency: each call is a full HTTP+MCP session handshake.
        _bench("3b. MCP server round-trip (live HTTP)", min(n, 200), min(workers, 8), _fn)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", type=int, default=1000, help="operations per benchmark")
    ap.add_argument("-w", "--workers", type=int, default=16, help="concurrent workers")
    ap.add_argument("--skip-mcp", action="store_true", help="skip the live MCP server test")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        _isolate(tmp)
        print(f"\nLoad test — n={args.n}, workers={args.workers}\n" + "-" * 96)
        bench_intake(args.n, args.workers)
        bench_full_pipeline(max(args.n // 10, 20), args.workers)  # heavier per op
        bench_recording_direct(args.n, args.workers)
        if not args.skip_mcp:
            bench_mcp_server(args.n, args.workers)
        print("-" * 96)
        print("Done.\n")


if __name__ == "__main__":
    main()
