from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.db import log_event
from src.gemini import get_model
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


_REGIME_PROMPT = """\
You are a aggressive BTC/USDT futures trading regime advisor.
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
        response = get_model("gemini-2.5-flash").generate_content(prompt)
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
        
        print(f"[regime] Gemini advisory agrees: {gemini_regime}")
        return regime        
    except Exception as e:
        print(f"[regime] Gemini review failed: {e} — using rule-based result")
        return regime


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
        state: dict = {}
        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass

        symbol_data = state.get("BTCUSDT", {})

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

        trend_range = symbol_data.get("trend_range", "")
        risk = symbol_data.get("risk", "")
        signal_summary = symbol_data.get("signal_summary", "")
        prev_regime = symbol_data.get("regime", "halt")

        regime = classify_regime(trend_range, risk)
        regime = review_regime_with_gemini(regime, trend_range, risk, signal_summary)

        regime_changed = regime != prev_regime
        regime_transition = (
            f"{prev_regime.upper()}_TO_{regime.upper()}" if regime_changed else ""
        )

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

        if regime_changed:
            log_event(
                "INFO", "regime",
                rs.regime_transition,
                extra={"prev_regime": rs.prev_regime, "regime": rs.regime, "symbol": rs.symbol},
            )

        change_str = f" [{regime_transition}]" if regime_changed else ""
        print(f"[regime] {rs.regime_updated_at} regime={regime}{change_str}")

    except Exception as e:
        print(f"[regime] run_regime_once error: {e}")
