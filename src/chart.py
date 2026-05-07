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
    bbl = float(row["BBL_20_2.0_2.0"])
    bbu = float(row["BBU_20_2.0_2.0"])

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


_SCORE_PROMPT_TEMPLATE = """\
You are a BTC/USDT futures trading signal validator.
Evaluate the rule-based signal and return a JSON confidence score.

## Signal
- entry_signal: {signal}
- trend_range: {trend_range}

## Last 10 Candles (oldest→newest)
{candles_json}

## Instructions
- Score how well the market data supports the signal (0=strongly against, 100=strongly supports).
- Return ONLY valid JSON, no markdown, no explanation outside JSON.

## Output Schema (strict)
{{"confidence": <integer 0-100>, "comment": "<one sentence in Korean>"}}
"""


def score_signal(df: pd.DataFrame, signal: str, trend_range: str) -> tuple[int, str]:
    try:
        cols = ["close", "EMA_20", "EMA_50", "EMA_200", "ADX_14", "RSI_14", "ATRr_14"]
        available = [c for c in cols if c in df.columns]
        last10 = df[available].tail(10).round(4).to_dict(orient="records")
        prompt = _SCORE_PROMPT_TEMPLATE.format(
            signal=signal,
            trend_range=trend_range,
            candles_json=json.dumps(last10, ensure_ascii=False),
        )
        client = genai.Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0]
        parsed = json.loads(text.strip())
        return int(parsed["confidence"]), str(parsed["comment"])
    except Exception:
        return 50, "LLM unavailable"


def update_state(signal: ChartSignal) -> None:
    # Load existing state (preserve other symbols)
    state: dict = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    # Read risk from explorer report
    risk = "risk-off"
    risk_summary = ""
    if REPORT_PATH.exists():
        try:
            report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
            risk = report.get("risk", "risk-off")
            risk_summary = report.get("summary", "")
        except Exception:
            pass

    now_iso = datetime.now(UTC).isoformat()

    state["meta"] = {
        "schema_version": 1,
        "updated_at": now_iso,
    }
    state[signal.symbol] = {
        "updated_at": signal.updated_at,
        "risk": risk,
        "risk_summary": risk_summary,
        "trend_range": signal.trend_range,
        "adx": signal.adx,
        "rsi": signal.rsi,
        "ema_aligned": signal.ema_aligned,
        "ema50_slope": signal.ema50_slope,
        "atr": signal.atr,
        "entry_signal": signal.entry_signal,
        "confidence": signal.confidence,
        "signal_summary": signal.signal_summary,
    }

    STATE_PATH.parent.mkdir(exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp_path), str(STATE_PATH))


def _read_prev_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _read_risk() -> tuple[str, str]:
    """Returns (risk, risk_summary). Falls back to risk-off if stale/missing."""
    if not REPORT_PATH.exists():
        return "risk-off", ""
    try:
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        ts_str = report.get("timestamp", "")
        if ts_str:
            ts = datetime.fromisoformat(ts_str)
            if (datetime.now(UTC) - ts) > timedelta(minutes=STALE_EXPLORER_MIN):
                return "risk-off", report.get("summary", "")
        return report.get("risk", "risk-off"), report.get("summary", "")
    except Exception:
        return "risk-off", ""


def run_chart_once() -> None:
    global _consecutive_failures

    try:
        prev_state = _read_prev_state()
        prev_symbol = prev_state.get("BTCUSDT", {})
        prev_trend_range = prev_symbol.get("trend_range", "range")

        risk, _ = _read_risk()

        df = fetch_candles()
        df = calc_indicators(df)

        trend_range = determine_trend_range(df, prev_trend_range)
        entry_signal = determine_entry(df, trend_range, risk)

        confidence, comment = score_signal(df, entry_signal, trend_range)
        if confidence < 60:
            entry_signal = "none"
            comment = f"[suppressed confidence={confidence}] {comment}"

        # Stale state.json check
        if prev_symbol:
            try:
                prev_updated = datetime.fromisoformat(prev_symbol.get("updated_at", ""))
                if (datetime.now(UTC) - prev_updated) > timedelta(minutes=STALE_CHART_MIN):
                    entry_signal = "none"
            except Exception:
                pass

        row = df.iloc[-1]
        signal = ChartSignal(
            symbol="BTCUSDT",
            updated_at=datetime.now(UTC).isoformat(),
            trend_range=trend_range,
            adx=round(float(row["ADX_14"]), 4),
            rsi=round(float(row["RSI_14"]), 4),
            ema_aligned=bool(row["EMA_20"] > row["EMA_50"] > row["EMA_200"]),
            ema50_slope=round(float(df["ema50_slope"].iloc[-1]), 6),
            atr=round(float(row["ATRr_14"]), 4),
            entry_signal=entry_signal,
            confidence=confidence,
            signal_summary=comment,
        )

        # Consecutive failure block
        if _consecutive_failures >= 3:
            signal.entry_signal = "none"
            print(f"[chart] consecutive_failures={_consecutive_failures} — entry_signal forced none")

        update_state(signal)
        _consecutive_failures = 0
        print(f"[chart] {signal.updated_at} trend={trend_range} signal={signal.entry_signal} conf={confidence}")

    except Exception as e:
        _consecutive_failures += 1
        print(f"[chart] run_chart_once error (consecutive={_consecutive_failures}): {e}")
