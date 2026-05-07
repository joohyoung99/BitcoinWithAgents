from __future__ import annotations

import logging
import threading
import time

_last_beat: float = time.time()
_lock = threading.Lock()


def _reset_for_tests() -> None:
    global _last_beat
    with _lock:
        _last_beat = time.time()


def beat() -> None:
    global _last_beat
    with _lock:
        _last_beat = time.time()


def start(scheduler, stale_minutes: int = 20, check_interval: float = 300.0) -> None:
    stale_seconds = stale_minutes * 60

    def _loop() -> None:
        while True:
            time.sleep(check_interval)  # wait one interval before first check to avoid false alarm at startup
            with _lock:
                age = time.time() - _last_beat
            if age > stale_seconds:
                logging.warning("[watchdog] heartbeat stale (%ds) — shutting down scheduler", int(age))
                try:
                    scheduler.shutdown(wait=False)
                except Exception as e:
                    logging.warning("[watchdog] shutdown failed: %s", e)
                return

    t = threading.Thread(target=_loop, daemon=True, name="watchdog")
    t.start()
