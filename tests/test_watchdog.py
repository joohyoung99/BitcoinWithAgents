import threading
import time
from unittest.mock import MagicMock

import pytest


def test_beat_resets_state():
    from src import watchdog
    watchdog._last_beat = 0.0
    watchdog.beat()
    assert watchdog._last_beat > 0.0


def test_start_detects_stale_and_calls_shutdown():
    from src import watchdog

    watchdog._last_beat = time.time() - 9999  # 오래된 heartbeat
    mock_scheduler = MagicMock()

    watchdog.start(mock_scheduler, stale_minutes=0, check_interval=0.05)
    time.sleep(0.3)  # watchdog 스레드가 체크할 시간 확보

    mock_scheduler.shutdown.assert_called_once_with(wait=False)


def test_start_does_not_shutdown_when_fresh():
    from src import watchdog

    watchdog.beat()  # 방금 갱신
    mock_scheduler = MagicMock()

    watchdog.start(mock_scheduler, stale_minutes=999, check_interval=0.05)
    time.sleep(0.3)

    mock_scheduler.shutdown.assert_not_called()
