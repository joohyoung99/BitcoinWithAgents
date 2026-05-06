from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pandas_ta as ta
import requests
from google import genai

from src.models import ChartSignal

BITGET_BASE = "https://api.bitget.com"
STATE_PATH = Path("data/state.json")
REPORT_PATH = Path("data/explorer_report.json")

STALE_CHART_MIN = 30
STALE_EXPLORER_MIN = 120

_consecutive_failures: int = 0


def fetch_candles(interval: str = "15min", limit: int = 500) -> pd.DataFrame:
    resp = requests.get(
        f"{BITGET_BASE}/api/v2/mix/market/candles",
        params={
            "symbol": "BTCUSDT",
            "productType": "USDT-FUTURES",
            "granularity": interval,
            "limit": str(limit),
        },
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()["data"]  # newest-first
    rows = list(reversed(rows))  # oldest-first
    df = pd.DataFrame(
        rows,
        columns=["timestamp", "open", "high", "low", "close", "base_vol", "quote_vol"],
    )
    df = df[["timestamp", "open", "high", "low", "close", "base_vol"]].rename(
        columns={"base_vol": "volume"}
    )
    for col in ["timestamp", "open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col]).astype("float64")
    df = df.iloc[:-1]  # drop incomplete last candle
    df = df.reset_index(drop=True)
    return df
