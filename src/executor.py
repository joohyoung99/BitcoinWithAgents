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
    ("normal", "long"): 10,
    ("normal", "short"): 5,
    ("caution", "long"): 5,
    ("caution", "short"): 3,
    ("risk_off_trend", "short"): 3,
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


def _bitget_get(client, endpoint: str, params: dict) -> dict | None:
    import urllib.parse
    rh = client.account.request_handler
    query_string = urllib.parse.urlencode(params)
    try:
        headers = rh._get_headers("GET", endpoint, query_string, "")
        resp = rh.session.get(f"{rh.base_url}{endpoint}", headers=headers, params=params)
        return resp.json()
    except Exception as e:
        print(f"[executor] GET {endpoint} failed: {e}")
        return None


def sync_exchange_positions(client, positions_data: dict, atr: float, regime: str) -> None:
    """거래소 실제 포지션을 positions.json에 동기화 (봇이 모르는 포지션 포함).

    흡수된 포지션은 즉시 SL + TP 거래소 등록.
    매 사이클마다 호출해도 안전 — synthetic_id 기반으로 중복 추가 방지.
    """
    resp = _bitget_get(client, "/api/v2/mix/position/all-position", {
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
    })
    if not resp or resp.get("code") != "00000":
        print(f"[executor] sync_exchange_positions failed: {resp}")
        return

    known_ids = {p.get("order_id", "") for p in positions_data["positions"]}

    for ex_pos in resp.get("data", []):
        if ex_pos.get("symbol") != SYMBOL:
            continue
        size = float(ex_pos.get("total", 0))
        if size <= 0:
            continue

        hold_side = ex_pos.get("holdSide", "long")  # "long" | "short"
        synthetic_id = f"synced_{SYMBOL}_{hold_side}"
        if synthetic_id in known_ids:
            continue

        entry_price = float(ex_pos.get("openPriceAvg", 0) or 0)
        if entry_price <= 0:
            continue

        leverage = int(float(ex_pos.get("leverage", 1) or 1))
        size_usdt = round((size * entry_price) / leverage, 4)
        sl_price = calc_sl_price(hold_side, entry_price, atr, regime) if atr > 0 else 0.0
        tp_price = calc_tp_price(hold_side, entry_price, atr, regime) if atr > 0 else 0.0

        record = {
            "order_id": synthetic_id,
            "direction": hold_side,
            "size_usdt": size_usdt,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "sl_order_id": "",
            "tp_order_id": "",
            "regime": regime,
            "leverage": leverage,
            "atr_at_entry": atr,
            "opened_at": datetime.now(UTC).isoformat(),
            "synced": True,
        }
        # 흡수 즉시 SL + TP 거래소 등록
        place_stop_loss_with_emergency(client, record)
        place_take_profit(client, record)

        positions_data["positions"].append(record)
        print(
            f"[executor] synced exchange pos: {hold_side} {size} BTC"
            f" entry={entry_price} sl={sl_price:.1f} tp={tp_price:.1f} id={synthetic_id}"
        )


def _qty_from_usdt(size_usdt: float, price: float, leverage: int) -> str:
    qty = (size_usdt * leverage) / price
    return f"{qty:.3f}"


def _set_leverage(client, direction: str, leverage: int) -> None:
    hold_side = "long" if direction == "long" else "short"
    resp = _bitget_post(client, "/api/v2/mix/account/set-leverage", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
        "leverage": str(leverage),
        "holdSide": hold_side,
    })
    if resp and resp.get("code") == "00000":
        print(f"[executor] leverage set: {hold_side} {leverage}x")
    else:
        print(f"[executor] set-leverage failed (non-fatal): {resp}")


def place_market_entry(
    client, direction: str, size_usdt: float, price: float, leverage: int,
    sl_price: float = 0.0, tp_price: float = 0.0,
) -> dict | None:
    _set_leverage(client, direction, leverage)
    side = "buy" if direction == "long" else "sell"
    qty = _qty_from_usdt(size_usdt, price, leverage)
    body: dict = {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "marginCoin": MARGIN_COIN,
        "size": qty,
        "side": side,
        "tradeSide": "open",
        "orderType": "market",
        "leverage": str(leverage),
    }
    if sl_price > 0:
        body["presetStopLossPrice"] = str(round(sl_price, 1))
    if tp_price > 0:
        body["presetStopSurplusPrice"] = str(round(tp_price, 1))

    for attempt in range(3):
        try:
            resp = _bitget_post(client, "/api/v2/mix/order/place-order", body)
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
    # hedge_mode: long 청산 = buy+close / short 청산 = sell+close
    close_side = "buy" if position["direction"] == "long" else "sell"
    btc_qty = _qty_from_usdt(position["size_usdt"], position["entry_price"], position["leverage"])
    resp = _bitget_post(client, "/api/v2/mix/order/place-order", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "marginCoin": MARGIN_COIN,
        "size": btc_qty,
        "side": close_side,
        "tradeSide": "close",
        "orderType": "market",
    })
    if not resp or resp.get("code") != "00000":
        print(f"[executor] close_market failed: {resp}")
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
    btc_qty = _qty_from_usdt(position["size_usdt"], position["entry_price"], position["leverage"])
    for attempt in range(3):
        try:
            resp = _bitget_post(client, "/api/v2/mix/order/place-tpsl-order", {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "marginCoin": MARGIN_COIN,
                "planType": "loss_plan",
                "triggerPrice": str(round(sl_price, 1)),
                "holdSide": hold_side,
                "size": btc_qty,
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
    close_side = "buy" if hold_side == "long" else "sell"
    btc_qty = _qty_from_usdt(position["size_usdt"], position["entry_price"], position["leverage"])
    _bitget_post(client, "/api/v2/mix/order/place-order", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "marginCoin": MARGIN_COIN,
        "size": btc_qty,
        "side": close_side,
        "tradeSide": "close",
        "orderType": "market",
    })
    return False


def place_take_profit(client, position: dict) -> bool:
    """Submit exchange-side TP. Non-fatal — check_tp_hits acts as fallback."""
    hold_side = position["direction"]
    tp_price = position["tp_price"]
    btc_qty = _qty_from_usdt(position["size_usdt"], position["entry_price"], position["leverage"])
    for attempt in range(3):
        try:
            resp = _bitget_post(client, "/api/v2/mix/order/place-tpsl-order", {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "marginCoin": MARGIN_COIN,
                "planType": "profit_plan",
                "triggerPrice": str(round(tp_price, 1)),
                "holdSide": hold_side,
                "size": btc_qty,
                "triggerType": "mark_price",
            })
            if resp and resp.get("code") == "00000":
                tp_order_id = (resp.get("data") or {}).get("orderId", "")
                position["tp_order_id"] = tp_order_id
                print(f"[executor] TP set: {hold_side} tp={tp_price:.1f} id={tp_order_id}")
                return True
            print(f"[executor] TP attempt {attempt+1} failed: {resp}")
        except Exception as e:
            print(f"[executor] TP attempt {attempt+1} exception: {e}")
        if attempt < 2:
            time.sleep(1)
    print("[executor] TP all retries failed — check_tp_hits will act as fallback")
    return False


def reconcile_closed_positions(
    client, positions_data: dict, daily: dict, current_price: float
) -> None:
    """거래소에서 SL/TP로 닫힌 포지션을 감지해 positions.json 정리 + trades.csv 기록."""
    if not positions_data["positions"]:
        return

    resp = _bitget_get(client, "/api/v2/mix/position/all-position", {
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
    })
    if not resp or resp.get("code") != "00000":
        return

    open_directions: set[str] = set()
    for ex_pos in resp.get("data", []):
        if ex_pos.get("symbol") == SYMBOL and float(ex_pos.get("total", 0)) > 0:
            open_directions.add(ex_pos.get("holdSide", ""))

    for pos in list(positions_data["positions"]):
        if pos["direction"] in open_directions:
            continue

        direction = pos["direction"]
        tp = pos["tp_price"]
        sl = pos["sl_price"]
        # 현재가 기준으로 TP/SL 중 어느 쪽이 체결됐는지 추정
        if direction == "long":
            close_price = tp if current_price >= tp * 0.995 else sl
            reason = "TP" if current_price >= tp * 0.995 else "SL"
        else:
            close_price = tp if current_price <= tp * 1.005 else sl
            reason = "TP" if current_price <= tp * 1.005 else "SL"

        pnl = _calc_pnl(pos, close_price)
        print(
            f"[executor] exchange closed {direction} order={pos['order_id']}"
            f" reason={reason} close≈{close_price:.1f} pnl={pnl:.2f}"
        )
        append_trade({
            "opened_at": pos["opened_at"],
            "closed_at": datetime.now(UTC).isoformat(),
            "direction": direction,
            "regime": pos["regime"],
            "leverage": pos["leverage"],
            "entry_price": pos["entry_price"],
            "close_price": close_price,
            "size_usdt": pos["size_usdt"],
            "sl_price": sl,
            "tp_price": tp,
            "realized_pnl_usdt": round(pnl, 4),
            "close_reason": reason,
            "atr_at_entry": pos["atr_at_entry"],
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
            p for p in positions_data["positions"] if p["order_id"] != pos["order_id"]
        ]


def check_tp_hits(client, positions_data: dict, daily: dict, current_price: float) -> None:
    """TP 폴백: 거래소 TP 등록 실패한 포지션을 봇이 직접 시장가 청산."""
    for pos in list(positions_data["positions"]):
        if pos.get("tp_order_id"):
            continue  # 거래소 TP 등록됨 — 거래소가 처리
        tp = pos["tp_price"]
        direction = pos["direction"]
        hit = current_price >= tp if direction == "long" else current_price <= tp
        if hit:
            print(f"[executor] TP fallback hit order={pos['order_id']} price={current_price} tp={tp}")
            close_position_market(client, pos, "TP", daily, positions_data)


def _tighten_sl(client, pos: dict, atr: float) -> None:
    """Tighten SL to ATR×0.8. New SL submitted first, then old SL cancelled (safe order)."""
    mult = 0.8
    entry = pos["entry_price"]
    new_sl = entry - atr * mult if pos["direction"] == "long" else entry + atr * mult

    btc_qty = _qty_from_usdt(pos["size_usdt"], pos["entry_price"], pos["leverage"])
    for attempt in range(3):
        try:
            resp = _bitget_post(client, "/api/v2/mix/order/place-tpsl-order", {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "marginCoin": MARGIN_COIN,
                "planType": "loss_plan",
                "triggerPrice": str(round(new_sl, 1)),
                "holdSide": pos["direction"],
                "size": btc_qty,
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

        # 2-1. 거래소에서 닫힌 포지션 정리 (SL/TP 체결 감지)
        if positions_data["positions"] and current_price > 0:
            reconcile_closed_positions(client, positions_data, daily, current_price)
            save_positions(positions_data)
            save_daily(daily)

        # 2-2. 거래소 포지션 동기화 (봇이 모르는 포지션 흡수)
        sync_exchange_positions(client, positions_data, atr, regime)
        save_positions(positions_data)

        # 3. TP 폴백 체크 (거래소 TP 등록 실패한 포지션만)
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
        print(
            f"[executor] regime={regime} signal={entry_signal} close={current_price:.1f}"
            f" atr={atr:.1f} positions={len(positions_data['positions'])}"
        )
        if entry_signal == "none":
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

        # 8. SL/TP 가격 사전 계산 (현재가 기준 — 시장가라 슬리피지 미미)
        sl_price = calc_sl_price(direction, current_price, atr, regime)
        tp_price = calc_tp_price(direction, current_price, atr, regime)

        # 9. Place market entry — SL/TP 주문과 동시 등록 (presetStopLossPrice)
        order_result = place_market_entry(
            client, direction, size_usdt, current_price, leverage,
            sl_price=sl_price, tp_price=tp_price,
        )
        if not order_result:
            print("[executor] entry order failed after retries — skip")
            return
        order_id = str(order_result.get("orderId", ""))
        print(f"[executor] order placed with preset SL={sl_price:.1f} TP={tp_price:.1f}")

        # 10. Poll order fill via REST (9s, every 3s — market fills fast)
        fill_data = None
        for _ in range(3):
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
            print(f"[executor] order {order_id} not filled in 9s — skip")
            return

        entry_price = float(fill_data.get("priceAvg", current_price))
        # 실제 체결가 기준으로 재계산
        sl_price = calc_sl_price(direction, entry_price, atr, regime)
        tp_price = calc_tp_price(direction, entry_price, atr, regime)

        # 11. 폴백: preset이 실패했을 경우 별도 SL/TP 등록 시도
        position_record = {
            "order_id": order_id,
            "direction": direction,
            "size_usdt": size_usdt,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "sl_order_id": "",
            "tp_order_id": "",
            "regime": regime,
            "leverage": leverage,
            "atr_at_entry": atr,
            "opened_at": datetime.now(UTC).isoformat(),
        }
        place_stop_loss_with_emergency(client, position_record)
        place_take_profit(client, position_record)

        # 12. Update positions.json
        positions_data["positions"].append(position_record)
        save_positions(positions_data)
        print(f"[executor] opened {direction} order={order_id} entry={entry_price:.1f} sl={sl_price:.1f} tp={tp_price:.1f}")

    except Exception as e:
        print(f"[executor] run_executor_once error: {e}")
