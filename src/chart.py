from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pandas_ta as ta
import requests

from src.gemini import get_model
from src.models import ChartSignal

BITGET_BASE = "https://api.bitget.com"
STATE_PATH = Path("data/state.json")
REPORT_PATH = Path("data/explorer_report.json")

STALE_CHART_MIN = 10
STALE_EXPLORER_MIN = 120

_consecutive_failures: int = 0


def fetch_candles(interval: str = "5m", limit: int = 500) -> pd.DataFrame:
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

    _CORE = ["EMA_20", "EMA_50", "ADX_14", "RSI_14", "ATRr_14"]
    df = df.dropna(subset=_CORE).reset_index(drop=True)

    # EMA50 slope: 3-candle rate of change via safe iloc indexing
    ema50 = df["EMA_50"]
    df["ema50_slope"] = (ema50.iloc[-1] - ema50.iloc[-4]) / ema50.iloc[-4]

    return df


def fetch_multi_timeframe() -> dict:
    """Fetch candles + indicators for 15m/1h/4h/1d timeframes."""
    result = {}
    timeframes = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
    for tf_name, interval in timeframes.items():
        try:
            df = fetch_candles(interval=interval, limit=300)
            df = calc_indicators(df)
            row = df.iloc[-1]
            ema200_raw = float(row["EMA_200"]) if "EMA_200" in row.index else float("nan")
            ema200 = ema200_raw if not math.isnan(ema200_raw) else float(row["EMA_50"])
            ema_aligned = (
                bool(row["EMA_20"] > row["EMA_50"] > ema200_raw)
                if not math.isnan(ema200_raw)
                else bool(row["EMA_20"] > row["EMA_50"])
            )
            result[tf_name] = {
                "close": round(float(row["close"]), 2),
                "ema20": round(float(row["EMA_20"]), 2),
                "ema50": round(float(row["EMA_50"]), 2),
                "ema200": round(ema200, 2),
                "adx": round(float(row["ADX_14"]), 2),
                "rsi": round(float(row["RSI_14"]), 2),
                "atr": round(float(row["ATRr_14"]), 2),
                "macd_hist": round(float(row.get("MACDh_12_26_9", 0)), 4),
                "bbu": round(float(row["BBU_20_2.0_2.0"]), 2),
                "bbl": round(float(row["BBL_20_2.0_2.0"]), 2),
                "ema_aligned": ema_aligned,
                "trend": "up" if float(row["EMA_20"]) > float(row["EMA_50"]) else "down",
            }
        except Exception as e:
            print(f"[chart] {tf_name} fetch failed: {e}")
            result[tf_name] = None
    return result


def determine_trend_range(df: pd.DataFrame, prev_trend_range: str) -> str:
    """Determine if market is in Trend or Range mode using ADX with hysteresis."""
    adx = float(df["ADX_14"].iloc[-1])
    slope = float(df["ema50_slope"].iloc[-1])

    if adx > 25:
        return "trend"
    if prev_trend_range == "trend" and adx > 22:
        return "trend"
    if adx < 20:
        return "range"
    return prev_trend_range  # hysteresis zone 20–22


def determine_entry(df: pd.DataFrame, trend_range: str, risk: str) -> str:
    """Determine entry signal based on trend/range mode and risk regime.

    Relaxed conditions for more aggressive trading:
    - Trend long: EMA20 > EMA50 sufficient (EMA200 not required), wider price/RSI range
    - Trend short: EMA20 < EMA50 sufficient
    - Range: Bollinger Band touch margin expanded to 1.0%, RSI extremes added
    """
    row = df.iloc[-1]
    close = float(row["close"])
    ema20 = float(row["EMA_20"])
    ema50 = float(row["EMA_50"])
    ema200 = float(row["EMA_200"])
    rsi = float(row["RSI_14"])
    bbl = float(row["BBL_20_2.0_2.0"])
    bbu = float(row["BBU_20_2.0_2.0"])
    macd_hist = float(row.get("MACDh_12_26_9", 0.0))

    # Halt: range + risk-off → still no new trades
    if trend_range == "range" and risk == "risk-off":
        return "none"

    if trend_range == "trend":
        if risk == "risk-off":
            # Short allowed if EMA20 < EMA50 (relaxed: EMA200 not required)
            if ema20 < ema50:
                return "short"
            return "none"
        # Normal: trend + risk-on
        # Long: EMA20 > EMA50 sufficient, price near or below EMA20, RSI 25-75
        if ema20 > ema50 and close <= ema20 * 1.005 and 25 <= rsi <= 75:
            return "long"
        # Long alternative: strong RSI momentum with EMA support
        if ema20 > ema50 and rsi > 50 and macd_hist > 0:
            return "long"
        # Short: EMA20 < EMA50 sufficient (relaxed from requiring EMA200)
        if ema20 < ema50:
            return "short"
        return "none"

    # Caution: range + risk-on (BB margin expanded from 0.2% to 1.0%)
    if close >= bbu * 0.990:
        return "short"
    if close <= bbl * 1.010:
        return "long"
    # RSI extreme as additional range signal
    if rsi >= 75:
        return "short"
    if rsi <= 25:
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
        response = get_model("gemini-2.5-flash").generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0]
        parsed = json.loads(text.strip())
        return int(parsed["confidence"]), str(parsed["comment"])
    except Exception:
        return 70, "LLM unavailable"


_LLM_SIGNAL_PROMPT = """\
You are an aggressive BTC/USDT futures trading AI.
The rule-based system found NO entry signal, but you should independently evaluate the market.

## Last 10 Candles (oldest→newest)
{candles_json}

## Current Market Context
- trend_range: {trend_range}
- risk: {risk}
- ADX: {adx}
- RSI: {rsi}
- EMA alignment (20>50>200): {ema_aligned}
- MACD histogram: {macd_hist}

## Instructions
- Evaluate if there is a viable trading opportunity that the rule-based system might have missed.
- Be AGGRESSIVE — look for emerging trends, momentum shifts, or mean-reversion setups.
- If you see any reasonable opportunity, suggest "long" or "short". Only return "none" if the market is truly directionless.
- Return ONLY valid JSON, no markdown, no explanation outside JSON.

## Output Schema (strict)
{{"signal": "<long|short|none>", "confidence": <integer 0-100>, "comment": "<one sentence in Korean>"}}
"""


def llm_generate_signal(
    df: pd.DataFrame, trend_range: str, risk: str,
) -> tuple[str, int, str]:
    """Ask LLM to independently evaluate market when rule-based gives no signal.

    Returns (signal, confidence, comment). Signal is used only if confidence >= 75.
    """
    try:
        cols = [
            "close", "EMA_20", "EMA_50", "EMA_200",
            "ADX_14", "RSI_14", "ATRr_14", "MACDh_12_26_9",
        ]
        available = [c for c in cols if c in df.columns]
        last10 = df[available].tail(10).round(4).to_dict(orient="records")
        row = df.iloc[-1]

        prompt = _LLM_SIGNAL_PROMPT.format(
            candles_json=json.dumps(last10, ensure_ascii=False),
            trend_range=trend_range,
            risk=risk,
            adx=round(float(row["ADX_14"]), 2),
            rsi=round(float(row["RSI_14"]), 2),
            ema_aligned=bool(row["EMA_20"] > row["EMA_50"] > row["EMA_200"]),
            macd_hist=round(float(row.get("MACDh_12_26_9", 0.0)), 4),
        )
        response = get_model("gemini-2.5-flash").generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0]
        parsed = json.loads(text.strip())
        signal = str(parsed.get("signal", "none"))
        confidence = int(parsed.get("confidence", 0))
        comment = str(parsed.get("comment", ""))

        if signal not in ("long", "short", "none"):
            return "none", 0, "invalid LLM signal"

        # Block long in risk-off trend
        if risk == "risk-off" and trend_range == "trend" and signal == "long":
            return "none", 0, "risk-off trend blocks long"

        return signal, confidence, comment
    except Exception as e:
        print(f"[chart] AI 독립 차트 분석 실패: {e}")
        return "none", 0, "LLM unavailable"


def update_state(signal: ChartSignal, multi_tf: dict | None = None) -> None:
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
        "close": signal.close,
        "entry_signal": signal.entry_signal,
        "confidence": signal.confidence,
        "signal_summary": signal.signal_summary,
    }
    if multi_tf:
        state["multi_tf"] = multi_tf

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

        if entry_signal == "none":
            # LLM override: ask Gemini for independent signal assessment
            llm_signal, llm_conf, llm_comment = llm_generate_signal(df, trend_range, risk)
            if llm_signal != "none" and llm_conf >= 75:
                entry_signal = llm_signal
                confidence, comment = llm_conf, f"[LLM override] {llm_comment}"
                print(f"[chart] AI 독립 차트 신호 발동: 신호={llm_signal} 신뢰도={llm_conf}")
            else:
                confidence, comment = 100, "no signal"
        else:
            confidence, comment = score_signal(df, entry_signal, trend_range)

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
            close=round(float(row["close"]), 2),
            entry_signal=entry_signal,
            confidence=confidence,
            signal_summary=comment,
        )

        # Consecutive failure block
        if _consecutive_failures >= 3:
            signal.entry_signal = "none"
            print(f"[chart] 연속실패={_consecutive_failures} — 진입 신호 강제 초기화")

        # Multi-timeframe data collection
        multi_tf = None
        try:
            multi_tf = fetch_multi_timeframe()
            aligned_count = sum(1 for v in multi_tf.values() if v and v.get("ema_aligned"))
            print(f"[chart] 멀티-TF 수집 완료: 정상={len([v for v in multi_tf.values() if v])}/4, 정배열={aligned_count}/4")
        except Exception as e:
            print(f"[chart] 멀티-TF 수집 실패: {e}")

        update_state(signal, multi_tf=multi_tf)
        _consecutive_failures = 0
        print(
            f"[chart] 추세={trend_range} 신호={signal.entry_signal} 신뢰도={confidence}"
            f" | 가격={signal.close:.1f} ADX={signal.adx:.1f} RSI={signal.rsi:.1f}"
            f" ATR={signal.atr:.1f} EMA정배열={signal.ema_aligned}"
        )

    except Exception as e:
        _consecutive_failures += 1
        print(f"[chart] 실행 에러 (연속실패={_consecutive_failures}): {e}")
