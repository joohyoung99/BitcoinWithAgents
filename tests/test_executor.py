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
    assert get_leverage("normal", "long") == 5
    assert get_leverage("normal", "short") == 3
    assert get_leverage("caution", "long") == 3
    assert get_leverage("caution", "short") == 2
    assert get_leverage("risk_off_trend", "short") == 2
