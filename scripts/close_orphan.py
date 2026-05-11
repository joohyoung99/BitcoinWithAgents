"""
Close the orphaned SHORT position left by test_trade.py runs.
Short close in hedge_mode = side:"sell" + tradeSide:"close"
"""
from __future__ import annotations

import json
import urllib.parse
import requests
from src.client import get_client

SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"


def _post(client, endpoint: str, body: dict) -> dict:
    rh = client.account.request_handler
    body_str = json.dumps(body)
    headers = rh._get_headers("POST", endpoint, "", body_str)
    resp = rh.session.post(f"{rh.base_url}{endpoint}", headers=headers, data=body_str)
    return resp.json()


def _get(client, endpoint: str, params: dict) -> dict:
    rh = client.account.request_handler
    query_string = urllib.parse.urlencode(params)
    headers = rh._get_headers("GET", endpoint, query_string, "")
    resp = rh.session.get(f"{rh.base_url}{endpoint}", headers=headers, params=params)
    return resp.json()


def get_all_positions(client) -> list[dict]:
    result = _get(client, "/api/v2/mix/position/all-position", {
        "productType": PRODUCT_TYPE,
        "marginCoin": MARGIN_COIN,
    })
    if result.get("code") != "00000":
        print("[error]", result)
        return []
    return result.get("data", [])


def get_price() -> float:
    resp = requests.get(
        "https://api.bitget.com/api/v2/mix/market/ticker",
        params={"symbol": SYMBOL, "productType": PRODUCT_TYPE},
        timeout=10,
    )
    return float(resp.json()["data"][0]["lastPr"])


def main() -> None:
    client = get_client()
    positions = get_all_positions(client)

    if not positions:
        print("[close_orphan] no open positions found")
        return

    for pos in positions:
        symbol = pos.get("symbol", "")
        size = float(pos.get("total", 0))
        hold_side = pos.get("holdSide", "")
        margin_mode = pos.get("marginMode", "isolated")

        if symbol != SYMBOL or size <= 0:
            continue

        price = get_price()
        # hedge_mode: short 청산 = sell + close
        close_side = "buy" if hold_side == "long" else "sell"

        print(f"[close_orphan] closing {hold_side} {size} BTC @ ~{price:.0f} (side={close_side})")

        result = _post(client, "/api/v2/mix/order/place-order", {
            "symbol": SYMBOL,
            "productType": PRODUCT_TYPE,
            "marginMode": margin_mode,
            "marginCoin": MARGIN_COIN,
            "size": str(size),
            "side": close_side,
            "tradeSide": "close",
            "orderType": "market",
        })

        print(f"[close_orphan] response: {result}")
        if result.get("code") == "00000":
            print(f"[close_orphan] SUCCESS — {hold_side} position closed")
        else:
            print(f"[close_orphan] FAILED — {result}")


if __name__ == "__main__":
    main()
