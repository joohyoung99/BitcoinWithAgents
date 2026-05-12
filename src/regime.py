from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.gemini import get_model
from src.models import RegimeState

STATE_PATH = Path("data/state.json")
STALE_REGIME_MIN = 10

_VALID_REGIMES = {"normal", "caution", "risk_off_trend", "halt"}

_REGIME_TABLE: dict[tuple[str, str], str] = {
    ("trend", "risk-on"):  "normal",
    ("range", "risk-on"):  "caution",
    ("trend", "risk-off"): "risk_off_trend",
    ("range", "risk-off"): "halt",
}

_REGIME_AGGRESSIVENESS = {
    "normal": 4,
    "caution": 3,
    "risk_off_trend": 2,
    "halt": 1,
}


def classify_regime(trend_range: str, risk: str) -> str:
    return _REGIME_TABLE.get((trend_range, risk), "halt")


_REGIME_PROMPT = """\
You are an AGGRESSIVE BTC/USDT futures trading regime advisor.
Your goal is to MAXIMIZE trading opportunities. Review the rule-based regime and suggest the most aggressive viable alternative.

## Market State
- trend_range: {trend_range}
- risk: {risk}
- chart_signal_summary: {signal_summary}

## Rule-Based Classification
- proposed_regime: {regime}

## Regime Definitions (aggressiveness: normal > caution > risk_off_trend > halt)
- normal: Trend + Risk-On → EMA Pullback long (5x) or short (3x) — MOST AGGRESSIVE
- caution: Range + Risk-On → BBands Mean Reversion long (3x) or short (2x)
- risk_off_trend: Trend + Risk-Off → Short only (2x)
- halt: Range + Risk-Off → No new trades — MOST CONSERVATIVE

## Instructions
- You are BIASED toward more aggressive regimes (normal > caution > risk_off_trend > halt).
- If there is ANY reasonable argument for a more aggressive regime, choose it.
- Prefer "normal" or "caution" over "risk_off_trend" or "halt" unless danger signals are overwhelming.
- Only choose "halt" if the market is genuinely dangerous with multiple confirmed risk signals.
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
            gemini_agg = _REGIME_AGGRESSIVENESS.get(gemini_regime, 0)
            rule_agg = _REGIME_AGGRESSIVENESS.get(regime, 0)

            if gemini_agg > rule_agg:
                # Gemini suggests more aggressive regime — adopt it
                print(f"[regime] AI가 더 공격적인 체제로 변경: 기존={regime} → AI={gemini_regime} — {comment}")
                return gemini_regime
            else:
                # Gemini suggests more conservative — keep rule-based
                print(f"[regime] AI가 보수적 체제 제안 (룰 우선으로 무시): 기존={regime} AI={gemini_regime} — {comment}")
                return regime

        print(f"[regime] AI 판단 일치: {gemini_regime}")
        return regime
    except Exception as e:
        print(f"[regime] AI 리뷰 실패: {e} — 기존 룰 기반 결과 사용")
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
            print("[regime] state.json 없음 — 안전모드(halt) 전환")
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
                    print("[regime] state.json 데이터 오래됨 — 안전모드(halt) 전환")
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

        change_str = f" [{regime_transition}]" if regime_changed else ""
        print(f"[regime] 현재={regime} 위험도={symbol_data.get('risk','?')} 추세={symbol_data.get('trend_range','?')}{change_str}")

    except Exception as e:
        print(f"[regime] 실행 에러: {e}")
