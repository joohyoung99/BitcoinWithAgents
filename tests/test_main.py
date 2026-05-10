from __future__ import annotations

import logging
from concurrent.futures import TimeoutError as FuturesTimeout
from unittest.mock import patch

import pytest

import main as main_mod

BALANCE_OK = {"total": 20000.0, "available": 18000.0, "unrealized_pnl": 500.0}


def test_main_calls_run_system_on_success():
    with patch.object(main_mod, "_healthcheck", return_value=BALANCE_OK), \
         patch("src.explorer.main") as mock_run:
        main_mod.main()
    mock_run.assert_called_once()


def test_main_exits_on_timeout(caplog):
    with patch.object(main_mod, "_healthcheck", side_effect=FuturesTimeout()), \
         caplog.at_level(logging.ERROR), \
         pytest.raises(SystemExit) as exc_info:
        main_mod.main()
    assert exc_info.value.code == 1
    assert "10초 초과" in caplog.text


def test_main_exits_on_value_error():
    with patch.object(main_mod, "_healthcheck", side_effect=ValueError("Missing env vars: BITGET_API_KEY")), \
         pytest.raises(SystemExit) as exc_info:
        main_mod.main()
    assert exc_info.value.code == 1


def test_main_exits_on_api_error():
    with patch.object(main_mod, "_healthcheck", side_effect=RuntimeError("Bitget API error 40001")), \
         pytest.raises(SystemExit) as exc_info:
        main_mod.main()
    assert exc_info.value.code == 1


def test_main_handles_keyboard_interrupt():
    with patch.object(main_mod, "_healthcheck", return_value=BALANCE_OK), \
         patch("src.explorer.main", side_effect=KeyboardInterrupt()):
        main_mod.main()  # KeyboardInterrupt가 밖으로 전파되면 안 됨
