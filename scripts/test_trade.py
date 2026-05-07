"""
One-shot trade test (SAFE VERSION)
- BUY → position confirm → WAIT → SELL
"""

from __future__ import annotations

import json
import time
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
def get_position(client) -> float:
    """
    return: position size (BTC)
    """
    endpoint = "/api/v2/mix/position/single-position"

    body = {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
    }

    result = _post(client, endpoint, body)

    if result.get("code") != "00000":
        print("[position] error:", result)
        return 0.0

    data = result.get("data", [])
    if not data:
        return 0.0

    try:
        return float(data[0].get("available", 0))
    except Exception:
        return 0.0


def wait_for_position(client, timeout=10) -> bool:
    """
    BUY 이후 포지션 생성 확인
    """
    for i in range(timeout):
        size = get_position(client)
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
        "holdSide": "short",
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


def place_market_sell(client, qty: float, price: float) -> None:
    print(f"[sell] market sell {qty} BTC @ ~{price:.0f}")

    result = _post(client, "/api/v2/mix/order/place-order", {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "marginCoin": MARGIN_COIN,
        "size": str(qty),
        "side": "sell",
        "tradeSide": "close",
        "orderType": "market",
        "leverage": str(LEVERAGE),
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
    size = get_position(client)
    if size <= 0:
        raise RuntimeError("no position before sell")

    price = get_current_price()
    place_market_sell(client, size, price)

    print("[done] trade complete")


if __name__ == "__main__":
    main()