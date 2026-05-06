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


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.ta.ema(length=20, append=True)   # → EMA_20
    df.ta.ema(length=50, append=True)   # → EMA_50
    df.ta.ema(length=200, append=True)  # → EMA_200
    df.ta.adx(length=14, append=True)   # → ADX_14, DMP_14, DMN_14
    df.ta.rsi(length=14, append=True)   # → RSI_14
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.atr(length=14, append=True)   # → ATRr_14
    df.ta.obv(append=True)              # → OBV

    df = df.dropna().reset_index(drop=True)

    # EMA50 slope: 3-candle rate of change via safe iloc indexing
    ema50 = df["EMA_50"]
    df["ema50_slope"] = (ema50.iloc[-1] - ema50.iloc[-4]) / ema50.iloc[-4]

    return df
