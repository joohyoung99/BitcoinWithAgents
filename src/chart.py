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


def determine_trend_range(df: pd.DataFrame, prev_trend_range: str) -> str:
    """Determine if market is in Trend or Range mode using ADX with hysteresis."""
    adx = float(df["ADX_14"].iloc[-1])
    slope = float(df["ema50_slope"].iloc[-1])

    if adx > 25 and slope > 0:
        return "trend"
    if prev_trend_range == "trend" and adx > 22:
        return "trend"
    if adx < 20:
        return "range"
    return prev_trend_range  # hysteresis zone 20–22


def determine_entry(df: pd.DataFrame, trend_range: str, risk: str) -> str:
    """Determine entry signal based on trend/range mode and risk regime."""
    row = df.iloc[-1]
    close = float(row["close"])
    ema20 = float(row["EMA_20"])
    ema50 = float(row["EMA_50"])
    ema200 = float(row["EMA_200"])
    rsi = float(row["RSI_14"])
    bbl = float(row["BBL_20_2.0"])
    bbu = float(row["BBU_20_2.0"])

    # Halt: range + risk-off
    if trend_range == "range" and risk == "risk-off":
        return "none"

    if trend_range == "trend":
        if risk == "risk-off":
            if ema20 < ema50 < ema200:
                return "short"
            return "none"
        # Normal: trend + risk-on
        if ema20 > ema50 > ema200 and ema50 <= close <= ema20 and 40 <= rsi <= 60:
            return "long"
        if ema20 < ema50 < ema200:
            return "short"
        return "none"

    # Caution: range + risk-on
    if close >= bbu:
        return "short"
    if close <= bbl:
        return "long"
    return "none"
