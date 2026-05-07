import threading
import time
from unittest.mock import MagicMock

import pytest

from src import watchdog


@pytest.fixture(autouse=True)
def reset_watchdog():
    watchdog._reset_for_tests()
    yield


def test_beat_resets_state():
    watchdog._last_beat = 0.0
    watchdog.beat()
    assert watchdog._last_beat > 0.0


def test_start_detects_stale_and_calls_shutdown():
    watchdog._last_beat = time.time() - 120  # clearly 2 minutes stale
    mock_scheduler = MagicMock()

    watchdog.start(mock_scheduler, stale_minutes=1, check_interval=0.05)
    time.sleep(0.3)

    mock_scheduler.shutdown.assert_called_once_with(wait=False)


def test_start_does_not_shutdown_when_fresh():
    watchdog.beat()  # 방금 갱신
    mock_scheduler = MagicMock()

    watchdog.start(mock_scheduler, stale_minutes=999, check_interval=0.05)
    time.sleep(0.3)

    mock_scheduler.shutdown.assert_not_called()
