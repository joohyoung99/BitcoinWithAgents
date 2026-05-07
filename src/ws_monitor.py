from __future__ import annotations

import csv
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Shared state with executor — keyed by order_id
fill_events: dict[str, threading.Event] = {}
fill_results: dict[str, dict] = {}

POSITIONS_PATH = Path("data/positions.json")
TRADES_PATH = Path("data/trades.csv")

TRADES_HEADER = [
    "opened_at", "closed_at", "direction", "regime", "leverage",
    "entry_price", "close_price", "size_usdt", "sl_price", "tp_price",
    "realized_pnl_usdt", "close_reason", "atr_at_entry",
]


def handle_order_event(data: dict) -> None:
    order_id = data.get("ordId", "")
    status = data.get("status", "")
    if status in ("full_fill", "partial_fill") and order_id in fill_events:
        fill_results[order_id] = data
        fill_events[order_id].set()


def _load_positions() -> dict:
    if POSITIONS_PATH.exists():
        try:
            return json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_transition": "", "positions": []}


def _save_positions(data: dict) -> None:
    POSITIONS_PATH.parent.mkdir(exist_ok=True)
    tmp = POSITIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(POSITIONS_PATH))


def handle_position_close_event(data: dict) -> None:
    pos_id = data.get("posId", "")
    pnl = float(data.get("achievedProfits", 0.0))
    close_price = float(data.get("averagePrice", 0.0))

    positions_data = _load_positions()
    matched = [p for p in positions_data["positions"] if p.get("order_id") == pos_id]
    if not matched:
        return

    pos = matched[0]
    TRADES_PATH.parent.mkdir(exist_ok=True)
    write_header = not TRADES_PATH.exists()
    row = {
        "opened_at": pos.get("opened_at", ""),
        "closed_at": datetime.now(UTC).isoformat(),
        "direction": pos.get("direction", ""),
        "regime": pos.get("regime", ""),
        "leverage": pos.get("leverage", ""),
        "entry_price": pos.get("entry_price", ""),
        "close_price": close_price,
        "size_usdt": pos.get("size_usdt", ""),
        "sl_price": pos.get("sl_price", ""),
        "tp_price": pos.get("tp_price", ""),
        "realized_pnl_usdt": round(pnl, 4),
        "close_reason": "SL_EXCHANGE",
        "atr_at_entry": pos.get("atr_at_entry", ""),
    }
    with TRADES_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    positions_data["positions"] = [
        p for p in positions_data["positions"] if p.get("order_id") != pos_id
    ]
    _save_positions(positions_data)
    print(f"[ws_monitor] SL_EXCHANGE closed pos={pos_id} pnl={pnl}")


def run_ws_monitor() -> None:
    api_key = os.getenv("BITGET_API_KEY", "")
    api_secret = os.getenv("BITGET_SECRET_KEY", "")
    passphrase = os.getenv("BITGET_PASSPHRASE", "")

    if not all([api_key, api_secret, passphrase]):
        print("[ws_monitor] API keys not set — WebSocket disabled")
        return

    try:
        from bitpy.ws_api import BitgetWebsocketAPI  # noqa: F401
        # Private channels (orders, positions) not yet supported in bitpy
        print("[ws_monitor] Private WebSocket channels not yet supported — WS monitor disabled")
        print("[ws_monitor] Fill detection uses REST polling in run_executor_once()")
        return
    except ImportError:
        print("[ws_monitor] bitpy.ws_api not available — WebSocket disabled")
        return
