from __future__ import annotations

import csv
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.client import get_client

STATE_PATH = Path("data/state.json")
POSITIONS_PATH = Path("data/positions.json")
DAILY_PATH = Path("data/daily.json")
TRADES_PATH = Path("data/trades.csv")

TRADES_HEADER = [
    "opened_at", "closed_at", "direction", "regime", "leverage",
    "entry_price", "close_price", "size_usdt", "sl_price", "tp_price",
    "realized_pnl_usdt", "close_reason", "atr_at_entry",
]

_DAILY_TEMPLATE = {
    "trade_count": 0,
    "wins": 0,
    "losses": 0,
    "consecutive_losses": 0,
    "daily_pnl_usdt": 0.0,
}


def load_positions() -> dict:
    if POSITIONS_PATH.exists():
        try:
            return json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_transition": "", "positions": []}


def save_positions(data: dict) -> None:
    POSITIONS_PATH.parent.mkdir(exist_ok=True)
    tmp = POSITIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(POSITIONS_PATH))


def load_daily() -> dict:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if DAILY_PATH.exists():
        try:
            data = json.loads(DAILY_PATH.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data
        except Exception:
            pass
    fresh = {"date": today, **_DAILY_TEMPLATE}
    save_daily(fresh)
    return fresh


def save_daily(data: dict) -> None:
    DAILY_PATH.parent.mkdir(exist_ok=True)
    tmp = DAILY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(DAILY_PATH))


def append_trade(row: dict) -> None:
    TRADES_PATH.parent.mkdir(exist_ok=True)
    write_header = not TRADES_PATH.exists()
    with TRADES_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in TRADES_HEADER})


_SL_MULT = {"normal": 1.5, "caution": 1.0, "risk_off_trend": 1.0}
_TP_MULT = {"normal": 3.0, "caution": 2.0, "risk_off_trend": 2.0}
_LEVERAGE = {
    ("normal", "long"): 5,
    ("normal", "short"): 3,
    ("caution", "long"): 3,
    ("caution", "short"): 2,
    ("risk_off_trend", "short"): 2,
}

MAX_POSITIONS = 2
DAILY_LOSS_LIMIT = -1500.0
MAX_CONSECUTIVE_LOSSES = 3


def calc_trade_size(balance: float) -> float:
    return min(2000.0, balance * 0.10)


def check_risk(positions: list, daily: dict, regime: str) -> tuple[bool, str]:
    if regime == "halt":
        return False, "halt"
    if len(positions) >= MAX_POSITIONS:
        return False, "max_positions"
    if daily.get("daily_pnl_usdt", 0.0) <= DAILY_LOSS_LIMIT:
        return False, "daily_loss"
    if daily.get("consecutive_losses", 0) >= MAX_CONSECUTIVE_LOSSES:
        return False, "consecutive"
    return True, ""


def get_leverage(regime: str, direction: str) -> int:
    return _LEVERAGE.get((regime, direction), 1)


def calc_sl_price(direction: str, entry: float, atr: float, regime: str) -> float:
    mult = _SL_MULT.get(regime, 1.0)
    return entry - atr * mult if direction == "long" else entry + atr * mult


def calc_tp_price(direction: str, entry: float, atr: float, regime: str) -> float:
    mult = _TP_MULT.get(regime, 2.0)
    return entry + atr * mult if direction == "long" else entry - atr * mult
