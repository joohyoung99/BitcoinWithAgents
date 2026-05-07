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


SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"


def _bitget_post(client, endpoint: str, body: dict) -> dict | None:
    import json as _json
    rh = client.account.request_handler
    body_str = _json.dumps(body)
    try:
        headers = rh._get_headers("POST", endpoint, "", body_str)
        resp = rh.session.post(f"{rh.base_url}{endpoint}", headers=headers, data=body_str)
        return resp.json()
    except Exception as e:
        print(f"[executor] POST {endpoint} failed: {e}")
        return None


def _qty_from_usdt(size_usdt: float, price: float, leverage: int) -> str:
    qty = (size_usdt * leverage) / price
    return f"{qty:.3f}"


def place_limit_order(client, direction: str, size_usdt: float, price: float, leverage: int) -> dict | None:
    side = "buy" if direction == "long" else "sell"
    qty = _qty_from_usdt(size_usdt, price, leverage)
    for attempt in range(3):
        try:
            resp = _bitget_post(client, "/api/v2/mix/order/place-order", {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "marginMode": "isolated",
                "marginCoin": MARGIN_COIN,
                "size": qty,
                "price": str(price),
                "side": side,
                "orderType": "limit",
                "leverage": str(leverage),
            })
            if resp and resp.get("code") == "00000":
                return resp.get("data")
            print(f"[executor] place_order attempt {attempt+1} failed: {resp}")
        except Exception as e:
            print(f"[executor] place_order attempt {attempt+1} exception: {e}")
        if attempt < 2:
            time.sleep(1)
    return None


def cancel_order(client, order_id: str) -> bool:
    resp = _bitget_post(client, "/api/v2/mix/order/cancel-order", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "orderId": order_id,
    })
    return bool(resp and resp.get("code") == "00000")


def _calc_pnl(position: dict, close_price: float) -> float:
    direction = position["direction"]
    entry = position["entry_price"]
    size_usdt = position["size_usdt"]
    leverage = position["leverage"]
    if direction == "long":
        return size_usdt * leverage * (close_price - entry) / entry
    return size_usdt * leverage * (entry - close_price) / entry


def close_position_market(client, position: dict, reason: str, daily: dict, positions_data: dict) -> bool:
    resp = _bitget_post(client, "/api/v2/mix/order/flash-close-positions", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "holdSide": position["direction"],
    })
    if not resp or resp.get("code") != "00000":
        print(f"[executor] flash_close failed: {resp}")
        return False

    now = datetime.now(UTC).isoformat()
    close_price = position.get("tp_price" if reason == "TP" else "sl_price", 0.0)
    pnl = _calc_pnl(position, close_price)

    append_trade({
        "opened_at": position["opened_at"],
        "closed_at": now,
        "direction": position["direction"],
        "regime": position["regime"],
        "leverage": position["leverage"],
        "entry_price": position["entry_price"],
        "close_price": close_price,
        "size_usdt": position["size_usdt"],
        "sl_price": position["sl_price"],
        "tp_price": position["tp_price"],
        "realized_pnl_usdt": round(pnl, 4),
        "close_reason": reason,
        "atr_at_entry": position["atr_at_entry"],
    })

    daily["daily_pnl_usdt"] = round(daily.get("daily_pnl_usdt", 0.0) + pnl, 4)
    daily["trade_count"] = daily.get("trade_count", 0) + 1
    if pnl >= 0:
        daily["wins"] = daily.get("wins", 0) + 1
        daily["consecutive_losses"] = 0
    else:
        daily["losses"] = daily.get("losses", 0) + 1
        daily["consecutive_losses"] = daily.get("consecutive_losses", 0) + 1

    positions_data["positions"] = [
        p for p in positions_data["positions"] if p["order_id"] != position["order_id"]
    ]
    return True


def place_stop_loss_with_emergency(client, position: dict) -> bool:
    """Submit exchange-side SL. On 3 failures → emergency market close."""
    hold_side = position["direction"]
    sl_price = position["sl_price"]
    for attempt in range(3):
        try:
            resp = _bitget_post(client, "/api/v2/mix/order/place-tpsl-order", {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "marginCoin": MARGIN_COIN,
                "planType": "loss_plan",
                "triggerPrice": str(sl_price),
                "holdSide": hold_side,
                "size": "0",
                "triggerType": "mark_price",
            })
            if resp and resp.get("code") == "00000":
                return True
            print(f"[executor] SL attempt {attempt+1} failed: {resp}")
        except Exception as e:
            print(f"[executor] SL attempt {attempt+1} exception: {e}")
        if attempt < 2:
            time.sleep(1)

    print("[executor] SL all retries failed — emergency market close")
    _bitget_post(client, "/api/v2/mix/order/flash-close-positions", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "holdSide": hold_side,
    })
    return False


def check_tp_hits(client, positions_data: dict, daily: dict, current_price: float) -> None:
    """Check if TP is hit for any position. If so, market close."""
    for pos in list(positions_data["positions"]):
        tp = pos["tp_price"]
        direction = pos["direction"]
        hit = current_price >= tp if direction == "long" else current_price <= tp
        if hit:
            print(f"[executor] TP hit order={pos['order_id']} price={current_price} tp={tp}")
            close_position_market(client, pos, "TP", daily, positions_data)


def _tighten_sl(client, pos: dict, atr: float) -> None:
    """Tighten SL to ATR×0.8. New SL submitted first, then old SL cancelled (safe order)."""
    mult = 0.8
    entry = pos["entry_price"]
    new_sl = entry - atr * mult if pos["direction"] == "long" else entry + atr * mult

    for attempt in range(3):
        try:
            resp = _bitget_post(client, "/api/v2/mix/order/place-tpsl-order", {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "marginCoin": MARGIN_COIN,
                "planType": "loss_plan",
                "triggerPrice": str(round(new_sl, 2)),
                "holdSide": pos["direction"],
                "size": "0",
                "triggerType": "mark_price",
            })
            if resp and resp.get("code") == "00000":
                # New SL confirmed → now cancel old SL
                if pos.get("sl_order_id"):
                    cancel_order(client, pos["sl_order_id"])
                new_sl_id = (resp.get("data") or {}).get("orderId", "")
                pos["sl_price"] = new_sl
                pos["sl_order_id"] = new_sl_id
                return
            print(f"[executor] tighten_sl attempt {attempt+1} failed: {resp}")
        except Exception as e:
            print(f"[executor] tighten_sl attempt {attempt+1} exception: {e}")
        if attempt < 2:
            time.sleep(1)
    print(f"[executor] tighten_sl failed — keeping old SL order={pos['order_id']}")


def handle_regime_change(
    client,
    positions_data: dict,
    daily: dict,
    new_regime: str,
    prev_regime: str,
    atr: float,
    current_price: float,
) -> None:
    """Handle regime transitions. Idempotent via last_transition check."""
    transition = f"{prev_regime.upper()}_TO_{new_regime.upper()}"

    if positions_data.get("last_transition") == transition:
        print(f"[executor] regime change {transition} already handled — skip")
        return

    halt_transitions = {
        "NORMAL_TO_HALT", "CAUTION_TO_HALT",
        "RISK_OFF_TREND_TO_HALT", "NORMAL_TO_RISK_OFF_TREND",
    }
    tighten_transitions = {"NORMAL_TO_CAUTION"}

    if transition in halt_transitions:
        for pos in list(positions_data["positions"]):
            pnl = _calc_pnl(pos, current_price)
            if pnl > 0:
                print(f"[executor] {transition}: closing profitable pos {pos['order_id']}")
                close_position_market(client, pos, "REGIME_CHANGE", daily, positions_data)
            else:
                print(f"[executor] {transition}: tightening SL pos {pos['order_id']}")
                _tighten_sl(client, pos, atr)

    elif transition in tighten_transitions:
        for pos in positions_data["positions"]:
            _tighten_sl(client, pos, atr)

    positions_data["last_transition"] = transition
    save_positions(positions_data)
    print(f"[executor] regime change handled: {transition}")


def run_executor_once() -> None:
    try:
        # 1. Read state.json
        if not STATE_PATH.exists():
            print("[executor] state.json missing — skip")
            return
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        sym = state.get("BTCUSDT", {})
        if not sym:
            print("[executor] no BTCUSDT in state.json — skip")
            return

        regime = sym.get("regime", "halt")
        entry_signal = sym.get("entry_signal", "none")
        atr = float(sym.get("atr", 0.0))
        current_price = float(sym.get("close", 0.0))
        regime_changed = sym.get("regime_changed", False)
        regime_transition = sym.get("regime_transition", "")
        prev_regime = sym.get("prev_regime", regime)

        # 2. Load positions + daily
        positions_data = load_positions()
        daily = load_daily()

        client = get_client()

        # 3. TP check (existing positions first)
        if positions_data["positions"] and current_price > 0:
            check_tp_hits(client, positions_data, daily, current_price)
            save_positions(positions_data)
            save_daily(daily)

        # 4. Regime change handling
        if regime_changed and regime_transition:
            handle_regime_change(
                client, positions_data, daily,
                new_regime=regime,
                prev_regime=prev_regime,
                atr=atr,
                current_price=current_price,
            )
            save_positions(positions_data)
            save_daily(daily)

        # 5. Risk checks
        ok, reason = check_risk(positions_data["positions"], daily, regime)
        if not ok:
            print(f"[executor] risk check failed: {reason} — skip entry")
            return

        # 6. Entry signal check
        if entry_signal == "none":
            print("[executor] entry_signal=none — skip")
            return

        direction = entry_signal  # "long" | "short"
        if regime == "risk_off_trend" and direction == "long":
            print("[executor] risk_off_trend: long blocked — skip")
            return

        # 7. Position sizing
        try:
            balance_data = client.account.get_accounts(PRODUCT_TYPE)
            if balance_data.code != "00000" or not balance_data.data:
                print("[executor] balance fetch failed — skip")
                return
            balance = float(balance_data.data[0]["available"])
        except Exception as e:
            print(f"[executor] balance fetch error: {e} — skip")
            return
        size_usdt = calc_trade_size(balance)
        leverage = get_leverage(regime, direction)

        # 8. Place limit entry (3 retry)
        order_result = place_limit_order(client, direction, size_usdt, current_price, leverage)
        if not order_result:
            print("[executor] entry order failed after retries — skip")
            return
        order_id = str(order_result.get("orderId", ""))

        # 9. Poll order fill via REST (30s, every 3s)
        fill_data = None
        for _ in range(10):
            time.sleep(3)
            try:
                detail = client.account.request_handler.request(
                    "GET", "/api/v2/mix/order/detail",
                    {"symbol": SYMBOL, "productType": PRODUCT_TYPE, "orderId": order_id},
                )
                status = (detail.get("data") or {}).get("status", "")
                if status == "full_fill":
                    fill_data = detail.get("data", {})
                    break
                elif status in ("cancelled", "cancel"):
                    print(f"[executor] order {order_id} cancelled externally — skip")
                    return
            except Exception as e:
                print(f"[executor] order detail poll error: {e}")
                continue

        if fill_data is None:
            print(f"[executor] order {order_id} not filled in 30s — cancelling")
            cancel_order(client, order_id)
            return

        entry_price = float(fill_data.get("priceAvg", current_price))
        sl_price = calc_sl_price(direction, entry_price, atr, regime)
        tp_price = calc_tp_price(direction, entry_price, atr, regime)

        # 10. Place exchange SL (3 retry, emergency close on all fail)
        position_record = {
            "order_id": order_id,
            "direction": direction,
            "size_usdt": size_usdt,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "sl_order_id": "",
            "regime": regime,
            "leverage": leverage,
            "atr_at_entry": atr,
            "opened_at": datetime.now(UTC).isoformat(),
        }
        sl_ok = place_stop_loss_with_emergency(client, position_record)
        if not sl_ok:
            print(f"[executor] SL failed + emergency close triggered for {order_id}")
            return

        # 11. Update positions.json
        positions_data["positions"].append(position_record)
        save_positions(positions_data)
        print(f"[executor] opened {direction} order={order_id} entry={entry_price} sl={sl_price} tp={tp_price}")

    except Exception as e:
        print(f"[executor] run_executor_once error: {e}")
