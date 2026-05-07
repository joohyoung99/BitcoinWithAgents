from __future__ import annotations

import threading
import time

_last_beat: float = time.time()
_lock = threading.Lock()


def beat() -> None:
    global _last_beat
    with _lock:
        _last_beat = time.time()


def start(scheduler, stale_minutes: int = 20, check_interval: float = 300.0) -> None:
    stale_seconds = stale_minutes * 60

    def _loop() -> None:
        while True:
            time.sleep(check_interval)
            with _lock:
                age = time.time() - _last_beat
            if age > stale_seconds:
                print(f"[watchdog] heartbeat stale ({age:.0f}s) — shutting down scheduler")
                try:
                    scheduler.shutdown(wait=False)
                except Exception as e:
                    print(f"[watchdog] shutdown failed: {e}")
                return

    t = threading.Thread(target=_loop, daemon=True, name="watchdog")
    t.start()
