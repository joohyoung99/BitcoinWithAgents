"""
One-shot trade test (SAFE VERSION)
- BUY → position confirm → WAIT → SELL
"""

from __future__ import annotations

import json
import time
import urllib.parse
import requests
from src.client import get_client

SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"
SIZE_USDT = 100.0
LEVERAGE = 1
WAIT_SECONDS = 30


# -------------------------
# API helper
# -------------------------
def _post(client, endpoint: str, body: dict) -> dict:
    rh = client.account.request_handler
    body_str = json.dumps(body)
    headers = rh._get_headers("POST", endpoint, "", body_str)
    resp = rh.session.post(
        f"{rh.base_url}{endpoint}",
        headers=headers,
        data=body_str,
    )
    return resp.json()


def _get(client, endpoint: str, params: dict) -> dict:
    rh = client.account.request_handler
    query_string = urllib.parse.urlencode(params)
    headers = rh._get_headers("GET", endpoint, query_string, "")
    resp = rh.session.get(
        f"{rh.base_url}{endpoint}",
        headers=headers,
        params=params,
    )
    return resp.json()


def get_current_price() -> float:
    resp = requests.get(
        "https://api.bitget.com/api/v2/mix/market/ticker",
        params={"symbol": SYMBOL, "productType": PRODUCT_TYPE},
        timeout=10,
    )
    return float(resp.json()["data"][0]["lastPr"])


# -------------------------
# Position check (핵심)
# -------------------------
def get_position(client) -> tuple[float, str, str]:
    """
    return: (position size in BTC, marginMode, holdSide)
    """
    endpoint = "/api/v2/mix/position/single-position"

    params = {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
    }

    result = _get(client, endpoint, params)

    if result.get("code") != "00000":
        print("[position] error:", result)
        return 0.0, "isolated", "long"

    data = result.get("data", [])
    if not data:
        return 0.0, "isolated", "long"

    try:
        pos = data[0]
        size = float(pos.get("total", 0))
        margin_mode = pos.get("marginMode", "isolated")
        hold_side = pos.get("holdSide", "long")
        print(f"[position] size={size} marginMode={margin_mode} holdSide={hold_side}")
        return size, margin_mode, hold_side
    except Exception:
        return 0.0, "isolated", "long"


def wait_for_position(client, timeout=10) -> bool:
    """
    BUY 이후 포지션 생성 확인
    """
    for i in range(timeout):
        size, _, _ = get_position(client)
        print(f"[position-check] size={size}")

        if size > 0:
            return True

        time.sleep(1)

    return False


# -------------------------
# Trading logic
# -------------------------
def set_leverage(client) -> None:
    result = _post(client, "/api/v2/mix/account/set-leverage", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
        "leverage": str(LEVERAGE),
        "holdSide": "long",
    })

    print(f"[leverage] {result.get('code')} {result.get('msg')}")
    if result.get("code") != "00000":
        raise RuntimeError(f"set-leverage failed: {result}")


def place_market_buy(client, price: float) -> tuple[str, float]:
    qty = round(SIZE_USDT / price, 5)

    print(f"[buy] market buy {qty} BTC @ ~{price:.0f}")

    result = _post(client, "/api/v2/mix/order/place-order", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "marginCoin": MARGIN_COIN,
        "size": str(qty),
        "side": "buy",
        "tradeSide": "open",
        "orderType": "market",
        "leverage": str(LEVERAGE),
    })

    print(f"[buy] response: {result}")

    if result.get("code") != "00000":
        raise RuntimeError(f"buy failed: {result}")

    return result["data"]["orderId"], qty


def place_market_sell(client, qty: float, price: float, margin_mode: str = "isolated", hold_side: str = "long") -> None:
    print(f"[sell] market sell {qty} BTC @ ~{price:.0f} (marginMode={margin_mode} holdSide={hold_side})")

    # hedge_mode: long 청산 = buy + close / short 청산 = sell + close
    close_side = "buy" if hold_side == "long" else "sell"
    result = _post(client, "/api/v2/mix/order/place-order", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginMode": margin_mode,
        "marginCoin": MARGIN_COIN,
        "size": str(qty),
        "side": close_side,
        "tradeSide": "close",
        "orderType": "market",
    })

    print(f"[sell] response: {result}")

    if result.get("code") != "00000":
        raise RuntimeError(f"sell failed: {result}")


# -------------------------
# Main flow (FIXED)
# -------------------------
def main() -> None:
    client = get_client()
    print("[test] Bitget demo client ready")

    price = get_current_price()
    print(f"[price] BTC = {price:.2f} USDT")

    set_leverage(client)

    order_id, qty = place_market_buy(client, price)
    print(f"[buy] orderId={order_id}, qty={qty}")

    # 🔥 핵심: 포지션 생성 확인
    print("[wait] waiting for position open...")
    if not wait_for_position(client, timeout=10):
        raise RuntimeError("position not opened after buy")

    print("[ok] position confirmed")

    # hold time (strategy placeholder)
    for remaining in range(WAIT_SECONDS, 0, -10):
        print(f"[hold] {remaining}s")
        time.sleep(10)

    # 🔥 SELL 전에 다시 확인
    size, margin_mode, hold_side = get_position(client)
    if size <= 0:
        raise RuntimeError("no position before sell")

    price = get_current_price()
    place_market_sell(client, size, price, margin_mode, hold_side)

    print("[done] trade complete")


if __name__ == "__main__":
    main()