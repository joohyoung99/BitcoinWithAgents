# Stage 4: Regime Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/regime.py` — reads `data/state.json`, classifies market regime (normal/caution/risk_off_trend/halt) via rules + Gemini advisory, writes regime fields back to `state.json` atomically, and integrates into the 15-min scheduler as a single serial job with chart.

**Architecture:** Single file `src/regime.py` with four flat functions mirroring chart.py's style. `src/models.py` gains `RegimeState`. `src/explorer.py` `main()` replaces the `"chart"` job with a `run_chart_and_regime` serial wrapper. Rules decide first; Gemini reviews and can log disagreement but never overrides.

**Tech Stack:** python-dotenv (existing), google-genai (existing), json/os/pathlib (stdlib), APScheduler (existing)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/models.py` | Modify | Add `RegimeState` dataclass |
| `src/regime.py` | Create | `classify_regime`, `review_regime_with_gemini`, `update_regime_state`, `run_regime_once` |
| `src/explorer.py` | Modify | Replace chart-only job with serial `run_chart_and_regime` wrapper |
| `tests/test_regime.py` | Create | 9 unit tests |

---

## Task 1: RegimeState dataclass

**Files:**
- Modify: `src/models.py`
- Create: `tests/test_regime.py` (first test only)

- [ ] **Step 1: Write failing test**

Create `tests/test_regime.py`:

```python
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from src.models import RegimeState


def test_regime_state_dataclass():
    rs = RegimeState(
        symbol="BTCUSDT",
        regime="normal",
        prev_regime="caution",
        regime_changed=True,
        regime_transition="CAUTION_TO_NORMAL",
        regime_summary="EMA 정배열 확인됨",
        regime_updated_at="2026-05-07T00:00:00+00:00",
    )
    assert rs.regime == "normal"
    assert rs.regime_changed is True
    assert rs.regime_transition == "CAUTION_TO_NORMAL"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_regime.py::test_regime_state_dataclass -v
```

Expected: FAIL — `ImportError: cannot import name 'RegimeState'`

- [ ] **Step 3: Add RegimeState to src/models.py**

Append to end of `src/models.py`:

```python


@dataclass
class RegimeState:
    symbol: str
    regime: str           # "normal" | "caution" | "risk_off_trend" | "halt"
    prev_regime: str
    regime_changed: bool
    regime_transition: str  # "CAUTION_TO_NORMAL" | "" (empty if no change)
    regime_summary: str
    regime_updated_at: str
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_regime.py::test_regime_state_dataclass -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/models.py tests/test_regime.py
git commit -m "feat: add RegimeState dataclass"
```

---

## Task 2: classify_regime

**Files:**
- Create: `src/regime.py` (skeleton + classify_regime)
- Modify: `tests/test_regime.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_regime.py`:

```python
def test_classify_normal():
    from src.regime import classify_regime
    assert classify_regime("trend", "risk-on") == "normal"


def test_classify_caution():
    from src.regime import classify_regime
    assert classify_regime("range", "risk-on") == "caution"


def test_classify_risk_off_trend():
    from src.regime import classify_regime
    assert classify_regime("trend", "risk-off") == "risk_off_trend"


def test_classify_halt():
    from src.regime import classify_regime
    assert classify_regime("range", "risk-off") == "halt"


def test_classify_unknown_defaults_halt():
    from src.regime import classify_regime
    assert classify_regime("unknown", "garbage") == "halt"
    assert classify_regime("trend", "") == "halt"
    assert classify_regime("", "risk-on") == "halt"
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_regime.py -k "classify" -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.regime'`

- [ ] **Step 3: Create src/regime.py with classify_regime**

Create `src/regime.py`:

```python
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google import genai

from src.models import RegimeState

STATE_PATH = Path("data/state.json")
STALE_REGIME_MIN = 30

_VALID_REGIMES = {"normal", "caution", "risk_off_trend", "halt"}

_REGIME_TABLE: dict[tuple[str, str], str] = {
    ("trend", "risk-on"):  "normal",
    ("range", "risk-on"):  "caution",
    ("trend", "risk-off"): "risk_off_trend",
    ("range", "risk-off"): "halt",
}


def classify_regime(trend_range: str, risk: str) -> str:
    return _REGIME_TABLE.get((trend_range, risk), "halt")
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_regime.py -k "classify" -v
```

Expected: all 7 assertions in 5 tests PASS

- [ ] **Step 5: Commit**

```
git add src/regime.py tests/test_regime.py
git commit -m "feat: regime classify_regime with fail-safe halt"
```

---

## Task 3: review_regime_with_gemini

**Files:**
- Modify: `src/regime.py`
- Modify: `tests/test_regime.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_regime.py`:

```python
def test_review_regime_gemini_agrees():
    from src.regime import review_regime_with_gemini

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"regime": "normal", "comment": "EMA 정배열 확인"}'
    mock_client.models.generate_content.return_value = mock_resp

    with patch("src.regime.genai.Client", return_value=mock_client):
        result = review_regime_with_gemini("normal", "trend", "risk-on", "EMA 상승중")

    assert result == "normal"


def test_review_regime_gemini_fails_returns_rule_regime():
    from src.regime import review_regime_with_gemini

    with patch("src.regime.genai.Client", side_effect=Exception("API error")):
        result = review_regime_with_gemini("caution", "range", "risk-on", "횡보")

    assert result == "caution"
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_regime.py::test_review_regime_gemini_agrees tests/test_regime.py::test_review_regime_gemini_fails_returns_rule_regime -v
```

Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement review_regime_with_gemini in src/regime.py**

Add after `classify_regime`:

```python
_REGIME_PROMPT = """\
You are a BTC/USDT futures trading regime advisor.
Review the rule-based regime classification and confirm or suggest an alternative.

## Market State
- trend_range: {trend_range}
- risk: {risk}
- chart_signal_summary: {signal_summary}

## Rule-Based Classification
- proposed_regime: {regime}

## Regime Definitions
- normal: Trend + Risk-On → EMA Pullback long (5x) or short (3x)
- caution: Range + Risk-On → BBands Mean Reversion long (3x) or short (2x)
- risk_off_trend: Trend + Risk-Off → Short only (2x)
- halt: Range + Risk-Off → No new trades

## Instructions
- Review if the proposed regime matches the market state.
- Return ONLY valid JSON, no markdown, no explanation outside JSON.

## Output Schema (strict)
{{"regime": "<normal|caution|risk_off_trend|halt>", "comment": "<one sentence in Korean>"}}
"""


def review_regime_with_gemini(
    regime: str,
    trend_range: str,
    risk: str,
    signal_summary: str,
) -> str:
    """Returns regime string (rule-based). Gemini is advisory only — disagreement is logged."""
    try:
        prompt = _REGIME_PROMPT.format(
            trend_range=trend_range,
            risk=risk,
            signal_summary=signal_summary,
            regime=regime,
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
        gemini_regime = str(parsed.get("regime", regime))
        comment = str(parsed.get("comment", ""))

        if gemini_regime not in _VALID_REGIMES:
            print(f"[regime] Gemini returned invalid regime={gemini_regime!r}, ignoring")
            return regime

        if gemini_regime != regime:
            print(f"[regime] Gemini advisory disagrees: rule={regime} gemini={gemini_regime} — {comment}")
            return regime  # rule always wins

        return regime
    except Exception as e:
        print(f"[regime] Gemini review failed: {e} — using rule-based result")
        return regime
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_regime.py::test_review_regime_gemini_agrees tests/test_regime.py::test_review_regime_gemini_fails_returns_rule_regime -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/regime.py tests/test_regime.py
git commit -m "feat: regime review_regime_with_gemini advisory"
```

---

## Task 4: update_regime_state + run_regime_once

**Files:**
- Modify: `src/regime.py`
- Modify: `tests/test_regime.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_regime.py`:

```python
def test_run_regime_once_writes_state(tmp_path, monkeypatch):
    import src.regime as regime_mod
    from src.regime import run_regime_once

    monkeypatch.setattr(regime_mod, "STATE_PATH", tmp_path / "state.json")

    # Write a valid state.json with chart data
    now_iso = datetime.now(UTC).isoformat()
    state = {
        "meta": {"schema_version": 1, "updated_at": now_iso},
        "BTCUSDT": {
            "updated_at": now_iso,
            "risk": "risk-on",
            "trend_range": "trend",
            "signal_summary": "EMA 상승",
        },
    }
    (tmp_path / "state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"regime": "normal", "comment": "정배열 확인"}'
    mock_client.models.generate_content.return_value = mock_resp

    with patch("src.regime.genai.Client", return_value=mock_client):
        run_regime_once()

    result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert result["BTCUSDT"]["regime"] == "normal"
    assert "regime_changed" in result["BTCUSDT"]
    assert "regime_updated_at" in result["BTCUSDT"]


def test_run_regime_once_detects_change(tmp_path, monkeypatch):
    import src.regime as regime_mod
    from src.regime import run_regime_once

    monkeypatch.setattr(regime_mod, "STATE_PATH", tmp_path / "state.json")

    now_iso = datetime.now(UTC).isoformat()
    state = {
        "meta": {"schema_version": 1, "updated_at": now_iso},
        "BTCUSDT": {
            "updated_at": now_iso,
            "risk": "risk-on",
            "trend_range": "trend",
            "signal_summary": "추세 진입",
            "regime": "caution",  # prev_regime = caution
        },
    }
    (tmp_path / "state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"regime": "normal", "comment": "정배열"}'
    mock_client.models.generate_content.return_value = mock_resp

    with patch("src.regime.genai.Client", return_value=mock_client):
        run_regime_once()

    result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert result["BTCUSDT"]["regime"] == "normal"
    assert result["BTCUSDT"]["regime_changed"] is True
    assert result["BTCUSDT"]["regime_transition"] == "CAUTION_TO_NORMAL"
    assert result["BTCUSDT"]["prev_regime"] == "caution"
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_regime.py -k "run_regime_once" -v
```

Expected: FAIL — `ImportError` for `run_regime_once`

- [ ] **Step 3: Implement update_regime_state and run_regime_once in src/regime.py**

Append to end of `src/regime.py`:

```python
def update_regime_state(rs: RegimeState) -> None:
    state: dict = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    symbol_data = state.get(rs.symbol, {})
    symbol_data.update({
        "regime": rs.regime,
        "prev_regime": rs.prev_regime,
        "regime_changed": rs.regime_changed,
        "regime_transition": rs.regime_transition,
        "regime_summary": rs.regime_summary,
        "regime_updated_at": rs.regime_updated_at,
    })
    state[rs.symbol] = symbol_data

    STATE_PATH.parent.mkdir(exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(STATE_PATH))


def run_regime_once() -> None:
    try:
        # 1. Read state.json
        state: dict = {}
        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass

        symbol_data = state.get("BTCUSDT", {})

        # 2. Stale or missing → halt immediately
        if not symbol_data:
            rs = RegimeState(
                symbol="BTCUSDT",
                regime="halt",
                prev_regime="halt",
                regime_changed=False,
                regime_transition="",
                regime_summary="state.json 없음 — fail-safe halt",
                regime_updated_at=datetime.now(UTC).isoformat(),
            )
            update_regime_state(rs)
            print("[regime] state.json missing — halt")
            return

        updated_at_str = symbol_data.get("updated_at", "")
        if updated_at_str:
            try:
                updated_at = datetime.fromisoformat(updated_at_str)
                if (datetime.now(UTC) - updated_at) > timedelta(minutes=STALE_REGIME_MIN):
                    rs = RegimeState(
                        symbol="BTCUSDT",
                        regime="halt",
                        prev_regime=symbol_data.get("regime", "halt"),
                        regime_changed=True,
                        regime_transition=f"{symbol_data.get('regime', 'halt').upper()}_TO_HALT",
                        regime_summary="state.json stale — fail-safe halt",
                        regime_updated_at=datetime.now(UTC).isoformat(),
                    )
                    update_regime_state(rs)
                    print("[regime] state.json stale — halt")
                    return
            except Exception:
                pass

        # 3. Classify
        trend_range = symbol_data.get("trend_range", "")
        risk = symbol_data.get("risk", "")
        signal_summary = symbol_data.get("signal_summary", "")
        prev_regime = symbol_data.get("regime", "halt")

        regime = classify_regime(trend_range, risk)

        # 4. Gemini advisory
        regime = review_regime_with_gemini(regime, trend_range, risk, signal_summary)

        # 5. Change detection
        regime_changed = regime != prev_regime
        regime_transition = (
            f"{prev_regime.upper()}_TO_{regime.upper()}" if regime_changed else ""
        )

        # 6. Build RegimeState and persist
        rs = RegimeState(
            symbol="BTCUSDT",
            regime=regime,
            prev_regime=prev_regime,
            regime_changed=regime_changed,
            regime_transition=regime_transition,
            regime_summary=signal_summary,
            regime_updated_at=datetime.now(UTC).isoformat(),
        )
        update_regime_state(rs)

        # 7. Log
        change_str = f" [{regime_transition}]" if regime_changed else ""
        print(f"[regime] {rs.regime_updated_at} regime={regime}{change_str}")

    except Exception as e:
        print(f"[regime] run_regime_once error: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_regime.py -k "run_regime_once" -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Run ALL regime tests**

```
uv run pytest tests/test_regime.py -v
```

Expected: all 9 tests PASS

- [ ] **Step 6: Commit**

```
git add src/regime.py tests/test_regime.py
git commit -m "feat: regime update_regime_state + run_regime_once"
```

---

## Task 5: Integrate into explorer.py scheduler

**Files:**
- Modify: `src/explorer.py` (main() function only)

현재 `main()`에서 `run_chart_once`를 15분 job으로 등록하고 있음. 이를 `run_chart_and_regime` 직렬 래퍼로 교체.

- [ ] **Step 1: Read explorer.py main() to find the exact current content**

```
uv run python -c "import inspect; from src.explorer import main; print(inspect.getsource(main))"
```

- [ ] **Step 2: Modify src/explorer.py main()**

`main()` 함수 전체를 아래로 교체:

```python
def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from src.chart import run_chart_once
    from src.regime import run_regime_once

    def run_chart_and_regime() -> None:
        run_chart_once()
        run_regime_once()

    print("[explorer] starting — running once immediately")
    run_once()
    run_chart_and_regime()

    now = datetime.now(UTC)
    scheduler = BlockingScheduler()
    scheduler.add_job(run_once, "interval", hours=1, id="explorer")
    scheduler.add_job(
        run_chart_and_regime,
        "interval",
        minutes=15,
        start_date=now + timedelta(minutes=5),
        id="chart_regime",
    )
    print("[explorer] scheduler started — explorer every 1h, chart+regime every 15min (Ctrl+C to stop)")
    scheduler.start()
```

- [ ] **Step 3: Verify existing explorer tests still pass**

```
uv run pytest tests/test_explorer.py -v
```

Expected: 기존 테스트 모두 PASS (main() 변경은 unit test에 영향 없음)

- [ ] **Step 4: Run full test suite**

```
uv run pytest -v
```

Expected: 모든 테스트 PASS (기존 1개 pre-existing ETF 실패 제외)

- [ ] **Step 5: Commit**

```
git add src/explorer.py
git commit -m "feat: wire chart+regime serial job into explorer main()"
```

---

## Post-Implementation Check

```
uv run pytest -v
uv run python -c "from src.regime import run_regime_once; print('import ok')"
```
