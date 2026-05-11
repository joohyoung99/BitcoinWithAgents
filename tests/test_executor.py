import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


def test_load_save_positions(tmp_path, monkeypatch):
    import src.executor as ex
    monkeypatch.setattr(ex, "POSITIONS_PATH", tmp_path / "positions.json")

    data = ex.load_positions()
    assert data == {"last_transition": "", "positions": []}

    data["positions"].append({"order_id": "1"})
    ex.save_positions(data)

    reloaded = ex.load_positions()
    assert reloaded["positions"][0]["order_id"] == "1"


def test_load_save_daily_resets_on_new_date(tmp_path, monkeypatch):
    import src.executor as ex
    monkeypatch.setattr(ex, "DAILY_PATH", tmp_path / "daily.json")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    yesterday_data = {
        "date": "2000-01-01",
        "trade_count": 5,
        "wins": 3,
        "losses": 2,
        "consecutive_losses": 2,
        "daily_pnl_usdt": -200.0,
    }
    (tmp_path / "daily.json").write_text(json.dumps(yesterday_data), encoding="utf-8")

    daily = ex.load_daily()
    assert daily["date"] == today
    assert daily["trade_count"] == 0
    assert daily["consecutive_losses"] == 0


def test_calc_trade_size():
    from src.executor import calc_trade_size
    assert calc_trade_size(20000.0) == 2000.0
    assert calc_trade_size(5000.0) == 500.0
    assert calc_trade_size(1000.0) == pytest.approx(100.0)
    assert calc_trade_size(100.0) == pytest.approx(10.0)


def test_check_risk_halt_regime():
    from src.executor import check_risk
    ok, reason = check_risk([], {"daily_pnl_usdt": 0.0, "consecutive_losses": 0}, "halt")
    assert ok is False
    assert reason == "halt"


def test_check_risk_max_positions():
    from src.executor import check_risk
    positions = [{"order_id": "1"}, {"order_id": "2"}]
    ok, reason = check_risk(positions, {"daily_pnl_usdt": 0.0, "consecutive_losses": 0}, "normal")
    assert ok is False
    assert reason == "max_positions"


def test_check_risk_daily_loss():
    from src.executor import check_risk
    ok, reason = check_risk([], {"daily_pnl_usdt": -1500.0, "consecutive_losses": 0}, "normal")
    assert ok is False
    assert reason == "daily_loss"


def test_check_risk_consecutive_losses():
    from src.executor import check_risk
    ok, reason = check_risk([], {"daily_pnl_usdt": 0.0, "consecutive_losses": 3}, "normal")
    assert ok is False
    assert reason == "consecutive"


def test_check_risk_passes():
    from src.executor import check_risk
    ok, reason = check_risk([], {"daily_pnl_usdt": 100.0, "consecutive_losses": 0}, "normal")
    assert ok is True
    assert reason == ""


def test_calc_sl_tp_normal_long():
    from src.executor import calc_sl_price, calc_tp_price
    # Normal: SL = ATR×1.5, TP = ATR×3.0
    assert calc_sl_price("long", 50000.0, 1000.0, "normal") == pytest.approx(48500.0)
    assert calc_tp_price("long", 50000.0, 1000.0, "normal") == pytest.approx(53000.0)


def test_calc_sl_tp_normal_short():
    from src.executor import calc_sl_price, calc_tp_price
    assert calc_sl_price("short", 50000.0, 1000.0, "normal") == pytest.approx(51500.0)
    assert calc_tp_price("short", 50000.0, 1000.0, "normal") == pytest.approx(47000.0)


def test_get_leverage():
    from src.executor import get_leverage
    assert get_leverage("normal", "long") == 10
    assert get_leverage("normal", "short") == 5
    assert get_leverage("caution", "long") == 5
    assert get_leverage("caution", "short") == 3
    assert get_leverage("risk_off_trend", "short") == 3


def test_place_entry_retry_succeeds_on_third():
    from src.executor import place_market_entry

    mock_client = MagicMock()

    with patch("src.executor._bitget_post") as mock_post, \
         patch("src.executor.time.sleep"):
        mock_post.side_effect = [
            {"code": "50001", "msg": "error"},
            {"code": "50001", "msg": "error"},
            {"code": "00000", "data": {"orderId": "abc123", "size": "0.02"}},
        ]
        result = place_market_entry(mock_client, "long", 1000.0, 50000.0, 5)

    assert result is not None
    assert result["orderId"] == "abc123"
    assert mock_post.call_count == 3


def test_place_entry_all_retries_fail():
    from src.executor import place_market_entry

    mock_client = MagicMock()

    with patch("src.executor._bitget_post") as mock_post, \
         patch("src.executor.time.sleep"):
        mock_post.return_value = {"code": "50001", "msg": "error"}
        result = place_market_entry(mock_client, "long", 1000.0, 50000.0, 5)

    assert result is None
    # 1 set-leverage call + 3 place-order retries
    assert mock_post.call_count == 4


def test_sl_failure_triggers_emergency_close():
    from src.executor import place_stop_loss_with_emergency

    mock_client = MagicMock()
    position = {
        "order_id": "abc",
        "direction": "long",
        "size_usdt": 1000.0,
        "entry_price": 50000.0,
        "sl_price": 48500.0,
        "tp_price": 53000.0,
        "sl_order_id": "",
        "regime": "normal",
        "leverage": 5,
        "atr_at_entry": 1000.0,
        "opened_at": "2026-05-07T00:00:00+00:00",
    }

    with patch("src.executor._bitget_post") as mock_post, \
         patch("src.executor.time.sleep"):
        # 3 SL retries fail, then emergency close succeeds
        mock_post.side_effect = [
            {"code": "50001"},
            {"code": "50001"},
            {"code": "50001"},
            {"code": "00000"},  # emergency flash_close
        ]
        result = place_stop_loss_with_emergency(mock_client, position)

    assert result is False
    assert mock_post.call_count == 4  # 3 SL + 1 emergency


def test_tp_hit_triggers_market_close(tmp_path, monkeypatch):
    import src.executor as ex
    from src.executor import check_tp_hits
    monkeypatch.setattr(ex, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(ex, "DAILY_PATH", tmp_path / "daily.json")
    monkeypatch.setattr(ex, "TRADES_PATH", tmp_path / "trades.csv")

    position = {
        "order_id": "tp1",
        "direction": "long",
        "size_usdt": 1000.0,
        "entry_price": 50000.0,
        "sl_price": 48500.0,
        "tp_price": 53000.0,
        "sl_order_id": "sl1",
        "regime": "normal",
        "leverage": 5,
        "atr_at_entry": 1000.0,
        "opened_at": "2026-05-07T00:00:00+00:00",
    }
    positions_data = {"last_transition": "", "positions": [position]}
    daily = {"date": "2026-05-07", "trade_count": 0, "wins": 0,
             "losses": 0, "consecutive_losses": 0, "daily_pnl_usdt": 0.0}

    mock_client = MagicMock()
    with patch("src.executor._bitget_post") as mock_post:
        mock_post.return_value = {"code": "00000"}
        check_tp_hits(mock_client, positions_data, daily, current_price=53001.0)

    mock_post.assert_called_once()
    assert len(positions_data["positions"]) == 0
    assert daily["wins"] == 1


def test_handle_regime_change_idempotent(tmp_path, monkeypatch):
    import src.executor as ex
    from src.executor import handle_regime_change
    monkeypatch.setattr(ex, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(ex, "DAILY_PATH", tmp_path / "daily.json")
    monkeypatch.setattr(ex, "TRADES_PATH", tmp_path / "trades.csv")

    positions_data = {"last_transition": "NORMAL_TO_HALT", "positions": []}
    daily = {"date": "2026-05-07", "trade_count": 0, "wins": 0,
             "losses": 0, "consecutive_losses": 0, "daily_pnl_usdt": 0.0}

    mock_client = MagicMock()
    with patch("src.executor._bitget_post") as mock_post:
        # Same transition already handled → no Bitget calls
        handle_regime_change(mock_client, positions_data, daily,
                             new_regime="halt", prev_regime="normal",
                             atr=1000.0, current_price=50000.0)

    mock_post.assert_not_called()
    # last_transition unchanged (already was NORMAL_TO_HALT)
    assert positions_data["last_transition"] == "NORMAL_TO_HALT"


def test_run_executor_once_skips_on_halt(tmp_path, monkeypatch):
    import src.executor as ex
    from src.executor import run_executor_once
    import json

    monkeypatch.setattr(ex, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(ex, "DAILY_PATH", tmp_path / "daily.json")
    monkeypatch.setattr(ex, "TRADES_PATH", tmp_path / "trades.csv")
    monkeypatch.setattr(ex, "STATE_PATH", tmp_path / "state.json")

    state = {
        "meta": {"schema_version": 1, "updated_at": "2026-05-07T12:00:00+00:00"},
        "BTCUSDT": {
            "updated_at": "2026-05-07T12:00:00+00:00",
            "regime": "halt",
            "entry_signal": "long",
            "confidence": 80,
            "atr": 1000.0,
            "regime_changed": False,
            "regime_transition": "",
            "close": 50000.0,
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    mock_client = MagicMock()
    with patch("src.executor.get_client", return_value=mock_client), \
         patch("src.executor._bitget_post") as mock_post:
        run_executor_once()

    mock_post.assert_not_called()
