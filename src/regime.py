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
