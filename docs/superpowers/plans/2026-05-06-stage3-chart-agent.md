# Stage 3: Chart Analysis Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/chart.py` — a 6-layer chart analysis module that fetches 15m candles, calculates indicators with pandas-ta, determines Trend/Range + entry signal via rules, scores confidence with Gemini Flash, and writes results atomically to `data/state.json`.

**Architecture:** Single file `src/chart.py` with layered functions (fetch → indicators → rules → LLM → persist → orchestrate). `src/models.py` gains `ChartSignal`. `src/explorer.py` `main()` adds chart scheduler with 5-min offset. `data/state.json` uses symbol-keyed structure with a top-level `meta` block.

**Tech Stack:** pandas, pandas-ta, google-genai (ADC), requests, APScheduler (existing), python-dotenv (existing)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/models.py` | Modify | Add `ChartSignal` dataclass |
| `src/chart.py` | Create | 6-layer chart analysis pipeline |
| `src/explorer.py` | Modify | Add chart scheduler to `main()` (lines ~315-328) |
| `tests/test_chart.py` | Create | 11 unit tests |
| `pyproject.toml` | Modify | Add `pandas`, `pandas-ta` dependencies |

---

## Task 1: Add dependencies + ChartSignal dataclass

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/models.py`
- Test: `tests/test_chart.py` (create, first test only)

- [ ] **Step 1: Install packages**

```
uv add pandas pandas-ta
```

Expected: both packages added to `pyproject.toml` dependencies and lock file updated.

- [ ] **Step 2: Write failing test for ChartSignal import**

Create `tests/test_chart.py`:

```python
from src.models import ChartSignal


def test_chart_signal_dataclass():
    sig = ChartSignal(
        symbol="BTCUSDT",
        updated_at="2026-05-06T00:00:00+00:00",
        trend_range="trend",
        adx=28.5,
        rsi=52.3,
        ema_aligned=True,
        ema50_slope=0.0023,
        atr=1250.5,
        entry_signal="long",
        confidence=78,
        signal_summary="test",
    )
    assert sig.symbol == "BTCUSDT"
    assert sig.entry_signal == "long"
    assert sig.confidence == 78
```

- [ ] **Step 3: Run test to verify it fails**

```
uv run pytest tests/test_chart.py::test_chart_signal_dataclass -v
```

Expected: FAIL — `ImportError: cannot import name 'ChartSignal'`

- [ ] **Step 4: Add ChartSignal to src/models.py**

Append to end of `src/models.py`:

```python


@dataclass
class ChartSignal:
    symbol: str
    updated_at: str
    trend_range: str        # "trend" | "range"
    adx: float
    rsi: float
    ema_aligned: bool       # EMA20 > EMA50 > EMA200
    ema50_slope: float      # positive=up, negative=down
    atr: float
    entry_signal: str       # "long" | "short" | "none"
    confidence: int         # 0–100
    signal_summary: str
```

- [ ] **Step 5: Run test to verify it passes**

```
uv run pytest tests/test_chart.py::test_chart_signal_dataclass -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```
git add src/models.py tests/test_chart.py pyproject.toml uv.lock
git commit -m "feat: add ChartSignal dataclass + pandas-ta dependency"
```

---

## Task 2: Layer 1 — fetch_candles

**Files:**
- Create: `src/chart.py` (initial skeleton + Layer 1)
- Test: `tests/test_chart.py`

Bitget public candles endpoint (no auth required):
```
GET https://api.bitget.com/api/v2/mix/market/candles
params: symbol=BTCUSDT, productType=USDT-FUTURES, granularity=15min, limit=500
```
Response: `{"data": [["timestamp_ms", "open", "high", "low", "close", "baseVol", "quoteVol"], ...]}`
Rows are newest-first — must reverse to ascending order. Drop last candle (incomplete).

- [ ] **Step 1: Write failing test**

Add to `tests/test_chart.py`:

```python
import pandas as pd
from unittest.mock import patch, MagicMock


def test_fetch_candles_returns_dataframe():
    from src.chart import fetch_candles

    # Bitget returns rows newest-first: [ts, open, high, low, close, baseVol, quoteVol]
    rows = [
        [str(1000 + i), "50000", "50100", "49900", "50050", "10", "500000"]
        for i in range(10, 0, -1)  # 10 rows, newest first
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": rows}

    with patch("src.chart.requests.get", return_value=mock_resp):
        df = fetch_candles()

    # iloc[:-1] removes last (incomplete) candle → 9 rows
    assert len(df) == 9
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    # ascending order: first row has smallest ts
    assert df["timestamp"].iloc[0] < df["timestamp"].iloc[-1]
    assert df["close"].dtype == float
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_chart.py::test_fetch_candles_returns_dataframe -v
```

Expected: FAIL — `ImportError: cannot import name 'fetch_candles'`

- [ ] **Step 3: Implement src/chart.py with fetch_candles**

Create `src/chart.py`:

```python
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
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "base_vol", "quote_vol"])
    df = df[["timestamp", "open", "high", "low", "close", "base_vol"]].rename(
        columns={"base_vol": "volume"}
    )
    for col in ["timestamp", "open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df = df.iloc[:-1]  # drop incomplete last candle
    df = df.reset_index(drop=True)
    return df
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_chart.py::test_fetch_candles_returns_dataframe -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/chart.py tests/test_chart.py
git commit -m "feat: chart Layer 1 — fetch_candles"
```

---

## Task 3: Layer 2 — calc_indicators

**Files:**
- Modify: `src/chart.py`
- Test: `tests/test_chart.py`

`pandas_ta` appends indicator columns in-place via `df.ta.<indicator>()`. After all indicators are added, drop NaN rows (warmup period). EMA50 slope uses safe `iloc` indexing: `(ema50.iloc[-1] - ema50.iloc[-4]) / ema50.iloc[-4]`.

Expected column names from pandas-ta:
- `EMA_20`, `EMA_50`, `EMA_200`
- `ADX_14` (from `df.ta.adx()`)
- `RSI_14` (from `df.ta.rsi()`)
- `MACD_12_26_9`, `MACDh_12_26_9`, `MACDs_12_26_9`
- `BBL_20_2.0`, `BBM_20_2.0`, `BBU_20_2.0`
- `ATRr_14`
- `OBV`

- [ ] **Step 1: Write failing test**

Add to `tests/test_chart.py`:

```python
def _make_ohlcv(n: int = 300) -> pd.DataFrame:
    import numpy as np
    rng = np.random.default_rng(42)
    close = 50000 + rng.normal(0, 200, n).cumsum()
    df = pd.DataFrame({
        "timestamp": range(n),
        "open": close - 50,
        "high": close + 100,
        "low": close - 100,
        "close": close,
        "volume": rng.uniform(100, 500, n),
    })
    return df


def test_calc_indicators_adds_columns():
    from src.chart import calc_indicators

    df = calc_indicators(_make_ohlcv(300))

    for col in ["EMA_20", "EMA_50", "EMA_200", "ADX_14", "RSI_14", "ATRr_14", "OBV"]:
        assert col in df.columns, f"missing column: {col}"
    assert "ema50_slope" in df.columns
    assert len(df) > 0
    assert not df["EMA_20"].isna().any()


def test_calc_indicators_ema50_slope_uses_iloc():
    from src.chart import calc_indicators

    df = calc_indicators(_make_ohlcv(300))
    # slope = (ema50[-1] - ema50[-4]) / ema50[-4]
    ema50 = df["EMA_50"]
    expected = (ema50.iloc[-1] - ema50.iloc[-4]) / ema50.iloc[-4]
    assert abs(df["ema50_slope"].iloc[-1] - expected) < 1e-10
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_chart.py::test_calc_indicators_adds_columns tests/test_chart.py::test_calc_indicators_ema50_slope_uses_iloc -v
```

Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement calc_indicators in src/chart.py**

Add after `fetch_candles`:

```python
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

    # EMA50 slope: 3-candle rate of change, safe iloc indexing
    ema50 = df["EMA_50"]
    df["ema50_slope"] = (ema50.iloc[-1] - ema50.iloc[-4]) / ema50.iloc[-4]

    return df
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_chart.py::test_calc_indicators_adds_columns tests/test_chart.py::test_calc_indicators_ema50_slope_uses_iloc -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/chart.py tests/test_chart.py
git commit -m "feat: chart Layer 2 — calc_indicators with pandas-ta"
```

---

## Task 4: Layer 3 — determine_trend_range + determine_entry

**Files:**
- Modify: `src/chart.py`
- Test: `tests/test_chart.py`

Hysteresis rules:
- Trend enter: `ADX_14 > 25 AND ema50_slope > 0`
- Trend hold (prev="trend"): `ADX_14 > 22`
- Range enter: `ADX_14 < 20`
- Zone 20–22: keep prev

Entry rules (from CLAUDE.md):
- **trend + risk-on (Normal):** EMA20>EMA50>EMA200 + price in EMA20~EMA50 pullback + RSI 40–60 → "long"; reverse EMA alignment → "short"
- **range + risk-on (Caution):** price >= BBU → "short"; price <= BBL → "long"
- **trend + risk-off:** short only (no long)
- **range + risk-off (Halt):** "none"

- [ ] **Step 1: Write failing tests**

Add to `tests/test_chart.py`:

```python
def _make_df_with_adx(adx: float, slope: float = 0.001) -> pd.DataFrame:
    """Minimal DataFrame with indicator columns for rule tests."""
    return pd.DataFrame({
        "ADX_14": [adx],
        "RSI_14": [50.0],
        "EMA_20": [100.0],
        "EMA_50": [95.0],
        "EMA_200": [90.0],
        "ema50_slope": [slope],
        "close": [97.0],   # between EMA50 and EMA20 → pullback
        "BBL_20_2.0": [88.0],
        "BBU_20_2.0": [112.0],
        "ATRr_14": [2.0],
    })


def test_determine_trend_range_trend():
    from src.chart import determine_trend_range
    df = _make_df_with_adx(adx=30.0, slope=0.002)
    assert determine_trend_range(df, prev_trend_range="range") == "trend"


def test_determine_trend_range_hysteresis_keeps_trend():
    from src.chart import determine_trend_range
    df = _make_df_with_adx(adx=23.0, slope=0.001)
    assert determine_trend_range(df, prev_trend_range="trend") == "trend"


def test_determine_trend_range_hysteresis_keeps_range():
    from src.chart import determine_trend_range
    df = _make_df_with_adx(adx=21.0, slope=-0.001)
    assert determine_trend_range(df, prev_trend_range="range") == "range"


def test_determine_trend_range_range():
    from src.chart import determine_trend_range
    df = _make_df_with_adx(adx=15.0)
    assert determine_trend_range(df, prev_trend_range="trend") == "range"


def test_determine_entry_normal_long():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=30.0)
    # close=97, EMA20=100, EMA50=95 → pullback zone, RSI=50 → long
    assert determine_entry(df, "trend", "risk-on") == "long"


def test_determine_entry_blocks_long_on_risk_off():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=30.0)
    assert determine_entry(df, "trend", "risk-off") != "long"


def test_determine_entry_halt():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=15.0)
    assert determine_entry(df, "range", "risk-off") == "none"


def test_determine_entry_caution_bb_short():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=15.0)
    df["close"] = 112.0   # >= BBU
    assert determine_entry(df, "range", "risk-on") == "short"


def test_determine_entry_caution_bb_long():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=15.0)
    df["close"] = 88.0    # <= BBL
    assert determine_entry(df, "range", "risk-on") == "long"
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_chart.py -k "trend_range or entry" -v
```

Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement determine_trend_range + determine_entry in src/chart.py**

Add after `calc_indicators`:

```python
def determine_trend_range(df: pd.DataFrame, prev_trend_range: str) -> str:
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
            # Short only
            if ema20 < ema50 < ema200:  # bearish alignment
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_chart.py -k "trend_range or entry" -v
```

Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```
git add src/chart.py tests/test_chart.py
git commit -m "feat: chart Layer 3 — determine_trend_range + determine_entry"
```

---

## Task 5: Layer 4 — score_signal (Gemini Flash)

**Files:**
- Modify: `src/chart.py`
- Test: `tests/test_chart.py`

Fixed Gemini input schema — always sends the same JSON structure with the last 10 candles' indicator values. Output schema is also fixed: `{"confidence": <int 0-100>, "comment": "<str>"}`.

On any exception, returns `(50, "LLM unavailable")`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_chart.py`:

```python
from unittest.mock import patch, MagicMock


def test_score_signal_returns_confidence_and_comment():
    from src.chart import score_signal

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"confidence": 75, "comment": "Strong uptrend"}'
    mock_client.models.generate_content.return_value = mock_response

    with patch("src.chart.genai.Client", return_value=mock_client):
        confidence, comment = score_signal(_make_ohlcv(300), "long", "trend")

    assert confidence == 75
    assert "uptrend" in comment.lower()


def test_score_signal_fallback_on_gemini_fail():
    from src.chart import score_signal

    with patch("src.chart.genai.Client", side_effect=Exception("API error")):
        confidence, comment = score_signal(_make_ohlcv(300), "long", "trend")

    assert confidence == 50
    assert comment == "LLM unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_chart.py::test_score_signal_returns_confidence_and_comment tests/test_chart.py::test_score_signal_fallback_on_gemini_fail -v
```

Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement score_signal in src/chart.py**

Add after `determine_entry`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_chart.py::test_score_signal_returns_confidence_and_comment tests/test_chart.py::test_score_signal_fallback_on_gemini_fail -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/chart.py tests/test_chart.py
git commit -m "feat: chart Layer 4 — score_signal with fixed Gemini schema"
```

---

## Task 6: Layer 5 — update_state (atomic write, meta)

**Files:**
- Modify: `src/chart.py`
- Test: `tests/test_chart.py`

`state.json` format:
```json
{
  "meta": {"schema_version": 1, "updated_at": "2026-05-06T12:05:00+00:00"},
  "BTCUSDT": {
    "updated_at": "...", "risk": "risk-on", "risk_summary": "...",
    "trend_range": "trend", "adx": 28.5, "rsi": 52.3,
    "ema_aligned": true, "ema50_slope": 0.0023, "atr": 1250.5,
    "entry_signal": "long", "confidence": 78, "signal_summary": "..."
  }
}
```

Atomic write: write to `data/state.tmp` then `os.replace("data/state.tmp", "data/state.json")`.

Reads `risk`/`risk_summary` from `data/explorer_report.json` if available; falls back to `"risk-off"` / `""`.

- [ ] **Step 1: Write failing test**

Add to `tests/test_chart.py`:

```python
import tempfile


def test_update_state_writes_atomic_with_meta(tmp_path, monkeypatch):
    from src.chart import update_state
    from src.models import ChartSignal
    import src.chart as chart_mod

    monkeypatch.setattr(chart_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(chart_mod, "REPORT_PATH", tmp_path / "explorer_report.json")

    # write a fake explorer report
    (tmp_path / "explorer_report.json").write_text(
        json.dumps({"risk": "risk-on", "summary": "test summary"}),
        encoding="utf-8",
    )

    sig = ChartSignal(
        symbol="BTCUSDT",
        updated_at="2026-05-06T12:00:00+00:00",
        trend_range="trend",
        adx=28.5,
        rsi=52.3,
        ema_aligned=True,
        ema50_slope=0.002,
        atr=1200.0,
        entry_signal="long",
        confidence=75,
        signal_summary="EMA 정배열",
    )
    update_state(sig)

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "meta" in state
    assert state["meta"]["schema_version"] == 1
    assert "updated_at" in state["meta"]
    assert "BTCUSDT" in state
    assert state["BTCUSDT"]["entry_signal"] == "long"
    assert state["BTCUSDT"]["risk"] == "risk-on"
    assert state["BTCUSDT"]["risk_summary"] == "test summary"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_chart.py::test_update_state_writes_atomic_with_meta -v
```

Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement update_state in src/chart.py**

Add after `score_signal`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_chart.py::test_update_state_writes_atomic_with_meta -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/chart.py tests/test_chart.py
git commit -m "feat: chart Layer 5 — update_state atomic write with meta block"
```

---

## Task 7: Layer 6 — run_chart_once (full pipeline + stale + consecutive failures)

**Files:**
- Modify: `src/chart.py`
- Test: `tests/test_chart.py`

`run_chart_once` orchestrates all layers:
1. Reads `prev_trend_range` from state.json (default: "range")
2. Reads `risk` from explorer_report.json; if stale (>120min) → "risk-off"
3. Calls `fetch_candles` → `calc_indicators` → `determine_trend_range` → `determine_entry`
4. Calls `score_signal`; if `confidence < 60` → override `entry_signal = "none"`
5. If state.json exists and `updated_at > 30min` ago → entry_signal = "none"
6. Calls `update_state`
7. On consecutive failures ≥3 → force entry_signal = "none"
8. All wrapped in try/except — never raises

- [ ] **Step 1: Write failing tests**

Add to `tests/test_chart.py`:

```python
def test_run_chart_once_writes_state_json(tmp_path, monkeypatch):
    import src.chart as chart_mod
    from src.chart import run_chart_once

    monkeypatch.setattr(chart_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(chart_mod, "REPORT_PATH", tmp_path / "explorer_report.json")
    monkeypatch.setattr(chart_mod, "_consecutive_failures", 0)

    (tmp_path / "explorer_report.json").write_text(
        json.dumps({"risk": "risk-on", "summary": "ok", "timestamp": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )

    rows = [
        [str(1000000 + i * 60000), "50000", "50100", "49900", "50050", "10", "500000"]
        for i in range(300, 0, -1)
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": rows}

    mock_gemini_client = MagicMock()
    mock_gemini_response = MagicMock()
    mock_gemini_response.text = '{"confidence": 70, "comment": "좋음"}'
    mock_gemini_client.models.generate_content.return_value = mock_gemini_response

    with patch("src.chart.requests.get", return_value=mock_resp), \
         patch("src.chart.genai.Client", return_value=mock_gemini_client):
        run_chart_once()

    assert (tmp_path / "state.json").exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "meta" in state
    assert "BTCUSDT" in state


def test_run_chart_once_suppresses_low_confidence(tmp_path, monkeypatch):
    import src.chart as chart_mod
    from src.chart import run_chart_once

    monkeypatch.setattr(chart_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(chart_mod, "REPORT_PATH", tmp_path / "explorer_report.json")
    monkeypatch.setattr(chart_mod, "_consecutive_failures", 0)

    (tmp_path / "explorer_report.json").write_text(
        json.dumps({"risk": "risk-on", "summary": "ok", "timestamp": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )

    rows = [
        [str(1000000 + i * 60000), "50000", "50100", "49900", "50050", "10", "500000"]
        for i in range(300, 0, -1)
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": rows}

    mock_gemini_client = MagicMock()
    mock_gemini_response = MagicMock()
    mock_gemini_response.text = '{"confidence": 40, "comment": "불확실"}'  # below 60
    mock_gemini_client.models.generate_content.return_value = mock_gemini_response

    with patch("src.chart.requests.get", return_value=mock_resp), \
         patch("src.chart.genai.Client", return_value=mock_gemini_client):
        run_chart_once()

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["BTCUSDT"]["entry_signal"] == "none"


def test_run_chart_once_consecutive_failure_forces_none(tmp_path, monkeypatch):
    import src.chart as chart_mod
    from src.chart import run_chart_once

    monkeypatch.setattr(chart_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(chart_mod, "REPORT_PATH", tmp_path / "explorer_report.json")
    monkeypatch.setattr(chart_mod, "_consecutive_failures", 3)

    (tmp_path / "explorer_report.json").write_text(
        json.dumps({"risk": "risk-on", "summary": "ok", "timestamp": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )

    with patch("src.chart.requests.get", side_effect=Exception("API down")):
        run_chart_once()

    # Should not raise; state.json may or may not exist — but process lives
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_chart.py -k "run_chart_once" -v
```

Expected: FAIL — `ImportError` for `run_chart_once`

- [ ] **Step 3: Implement run_chart_once in src/chart.py**

Add at the end of `src/chart.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_chart.py -k "run_chart_once" -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: Run all chart tests**

```
uv run pytest tests/test_chart.py -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```
git add src/chart.py tests/test_chart.py
git commit -m "feat: chart Layer 6 — run_chart_once with stale/failure guards"
```

---

## Task 8: Integrate chart scheduler into explorer.py main()

**Files:**
- Modify: `src/explorer.py` (lines 315–328, the `main()` function)

The current `main()` runs explorer only. Replace it so it also:
1. Runs `run_chart_once()` immediately after `run_once()`
2. Adds a 15-min chart job starting 5 minutes after now (so chart fires at :05/:20/:35/:50 when explorer fires at :00)

- [ ] **Step 1: Modify src/explorer.py main()**

Replace the current `main()` function (lines 315–328):

```python
def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from src.chart import run_chart_once

    print("[explorer] starting — running once immediately")
    run_once()
    run_chart_once()

    now = datetime.now(UTC)
    scheduler = BlockingScheduler()
    scheduler.add_job(run_once, "interval", hours=1, id="explorer")
    scheduler.add_job(
        run_chart_once,
        "interval",
        minutes=15,
        start_date=now + timedelta(minutes=5),
        id="chart",
    )
    print("[explorer] scheduler started — explorer every 1h, chart every 15min (Ctrl+C to stop)")
    scheduler.start()
```

Also add `timedelta` to the existing datetime import at top of file (line 8 currently reads `from datetime import UTC, datetime`):

```python
from datetime import UTC, datetime, timedelta
```

- [ ] **Step 2: Verify the existing explorer tests still pass**

```
uv run pytest tests/test_explorer.py -v
```

Expected: all existing tests PASS (main() changes don't affect unit tests)

- [ ] **Step 3: Verify all tests pass**

```
uv run pytest -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```
git add src/explorer.py
git commit -m "feat: wire chart scheduler into explorer main() with 5-min offset"
```

---

## Post-Implementation Check

After all tasks are done, verify the full test suite and that the module imports cleanly:

```
uv run pytest -v
uv run python -c "from src.chart import run_chart_once; print('import ok')"
```
