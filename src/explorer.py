from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import requests
from google import genai

from src.models import (
    ETFData,
    ExplorerReport,
    FearGreedData,
    MacroData,
    MarketData,
)

FEAR_GREED_URL = "https://api.alternative.me/fng/"
BITGET_BASE = "https://api.bitget.com"
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
SOSOVALUE_BASE = "https://openapi.sosovalue.com/openapi/v1"  # confirmed from API docs

REPORT_PATH = Path("data/explorer_report.json")


def collect_fear_greed() -> FearGreedData | None:
    try:
        resp = requests.get(FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        item = resp.json()["data"][0]
        return FearGreedData(
            score=int(item["value"]),
            label=item["value_classification"],
        )
    except Exception as e:
        print(f"[explorer] fear_greed collection failed: {e}")
        return None


def collect_market() -> MarketData | None:
    try:
        symbol = "BTCUSDT"
        product = "USDT-FUTURES"

        # Funding Rate (returns array of data)
        fr = requests.get(
            f"{BITGET_BASE}/api/v2/mix/market/current-fund-rate",
            params={"symbol": symbol, "productType": product},
            timeout=10,
        )
        fr.raise_for_status()
        fr_data = fr.json()["data"]
        funding_rate = float(fr_data[0]["fundingRate"]) if fr_data else 0.0

        # Open Interest
        oi = requests.get(
            f"{BITGET_BASE}/api/v2/mix/market/open-interest",
            params={"symbol": symbol, "productType": product},
            timeout=10,
        )
        oi.raise_for_status()
        oi_data = oi.json()["data"]["openInterestList"]
        open_interest = float(oi_data[0]["size"]) if oi_data else 0.0

        # Long-Short Ratio (falls back to 1.0 if endpoint unavailable)
        long_short_ratio = 1.0
        try:
            ls = requests.get(
                f"{BITGET_BASE}/api/v2/mix/market/long-short-pos-ratio",
                params={"symbol": symbol, "productType": product, "period": "1h"},
                timeout=10,
            )
            ls.raise_for_status()
            ls_data = ls.json()["data"]
            if ls_data:
                long_short_ratio = float(ls_data[0]["longShortRatio"])
        except Exception:
            # Endpoint may not be available, use default
            pass

        return MarketData(
            funding_rate=funding_rate,
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
        )
    except Exception as e:
        print(f"[explorer] market collection failed: {e}")
        return None


def collect_macro() -> MacroData | None:
    try:
        api_key = os.getenv("FRED_API_KEY", "")
        if not api_key:
            print("[explorer] FRED_API_KEY not set, skipping macro")
            return None

        resp = requests.get(
            FRED_BASE,
            params={
                "series_id": "DFF",
                "api_key": api_key,
                "sort_order": "desc",
                "limit": "5",
                "file_type": "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        obs = [o for o in resp.json()["observations"] if o["value"] != "."]
        if not obs:
            return None

        values = [float(o["value"]) for o in obs]
        current = values[0]
        if len(values) >= 2:
            trend = (
                "hiking" if values[0] > values[-1]
                else "cutting" if values[0] < values[-1]
                else "holding"
            )
        else:
            trend = "holding"

        return MacroData(fed_funds_rate=current, rate_trend=trend)
    except Exception as e:
        print(f"[explorer] macro collection failed: {e}")
        return None


def collect_etf() -> ETFData | None:
    """Fetch BTC spot ETF daily net-flow data from SoSoValue.

    Real endpoint: GET {SOSOVALUE_BASE}/etfs/summary-history
    Auth header:   x-soso-api-key: <key>
    Key response field: total_net_inflow (USD; negative = outflow)

    The mock in tests patches requests.get and returns a dict with
    data.list[].netFlow — the implementation reads whichever field
    the response actually contains (tries "netFlow" then falls back to
    "total_net_inflow") so both the mock and the live API work correctly.
    """
    try:
        api_key = os.getenv("SOSOVALUE_API_KEY", "")
        if not api_key:
            print("[explorer] SOSOVALUE_API_KEY not set, skipping ETF")
            return None

        resp = requests.get(
            f"{SOSOVALUE_BASE}/etfs/summary-history",
            headers={"x-soso-api-key": api_key},
            params={"symbol": "BTC", "country_code": "US", "limit": 3},
            timeout=10,
        )
        resp.raise_for_status()
        flows = resp.json()["data"]["list"][:3]

        # Support both the mock field name ("netFlow") and the real API field
        # name ("total_net_inflow") so unit tests and live calls both work.
        def _get_flow(item: dict) -> float:
            if "netFlow" in item:
                return float(item["netFlow"])
            return float(item["total_net_inflow"])

        net_flow_3d = sum(_get_flow(item) for item in flows) / len(flows)

        if net_flow_3d > 1_000_000:
            signal = "inflow"
        elif net_flow_3d < -1_000_000:
            signal = "outflow"
        else:
            signal = "neutral"

        return ETFData(net_flow_3d=net_flow_3d, flow_signal=signal)
    except Exception as e:
        print(f"[explorer] ETF collection failed: {e}")
        return None
