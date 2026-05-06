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
SOSOVALUE_BASE = "https://api.sosovalue.com"  # SoSoValue 문서 확인 후 조정

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
