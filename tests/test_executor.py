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
