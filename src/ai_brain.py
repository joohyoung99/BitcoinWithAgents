"""AI Decision Engine — Gemini 기반 방향/신뢰도 판단 모듈.

AI는 direction(방향)과 confidence(신뢰도)만 결정.
레버리지·사이즈·최종 승인은 Risk Engine이 담당.
"""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from src.gemini import get_model

TRADES_PATH = Path("data/trades.csv")


# ---------------------------------------------------------------------------
# Trade history analysis
# ---------------------------------------------------------------------------

def summarize_trade_history(limit: int = 20) -> dict:
    """최근 거래 이력을 분석하여 패턴 요약."""
    trades: list[dict] = []
    try:
        if TRADES_PATH.exists():
            with TRADES_PATH.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    trades.append(row)
    except Exception:
        pass

    recent = trades[-limit:]
    if not recent:
        return {"total": 0, "summary": "거래 이력 없음"}

    wins = [t for t in recent if float(t.get("realized_pnl_usdt", 0)) >= 0]

    regime_stats: dict = {}
    direction_stats: dict = {}
    for t in recent:
        regime = t.get("regime", "unknown")
        direction = t.get("direction", "unknown")
        pnl = float(t.get("realized_pnl_usdt", 0))

        regime_stats.setdefault(regime, {"count": 0, "pnl": 0.0, "wins": 0})
        regime_stats[regime]["count"] += 1
        regime_stats[regime]["pnl"] = round(regime_stats[regime]["pnl"] + pnl, 2)
        if pnl >= 0:
            regime_stats[regime]["wins"] += 1

        direction_stats.setdefault(direction, {"count": 0, "pnl": 0.0, "wins": 0})
        direction_stats[direction]["count"] += 1
        direction_stats[direction]["pnl"] = round(direction_stats[direction]["pnl"] + pnl, 2)
        if pnl >= 0:
            direction_stats[direction]["wins"] += 1

    total_pnl = sum(float(t.get("realized_pnl_usdt", 0)) for t in recent)

    return {
        "total": len(recent),
        "wins": len(wins),
        "losses": len(recent) - len(wins),
        "win_rate": round(len(wins) / len(recent) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "regime_stats": regime_stats,
        "direction_stats": direction_stats,
        "recent_5": [
            {
                "direction": t.get("direction"),
                "regime": t.get("regime"),
                "pnl": round(float(t.get("realized_pnl_usdt", 0)), 2),
                "close_reason": t.get("close_reason"),
                "leverage": t.get("leverage"),
            }
            for t in recent[-5:]
        ],
    }


# ---------------------------------------------------------------------------
# Prompt formatting helpers
# ---------------------------------------------------------------------------

def _format_multi_tf(multi_tf: dict) -> str:
    lines = []
    for tf in ["15m", "1h", "4h", "1d"]:
        data = multi_tf.get(tf)
        if not data:
            lines.append(f"### {tf}: 데이터 없음")
            continue
        align = "aligned ✅" if data.get("ema_aligned") else "not aligned ❌"
        trend = "↑" if data.get("trend") == "up" else "↓"
        lines.append(
            f"### {tf}\n"
            f"close={data['close']:,.1f} | "
            f"EMA 20/50/200={data['ema20']:,.1f}/{data['ema50']:,.1f}/{data['ema200']:,.1f} ({align}) | "
            f"ADX={data['adx']:.1f} | RSI={data['rsi']:.1f} | "
            f"MACD_hist={data['macd_hist']:+.4f} | ATR={data['atr']:.1f} | "
            f"BB={data['bbl']:,.1f}~{data['bbu']:,.1f} | Trend={trend}"
        )
    return "\n".join(lines)


def _format_positions(positions: list) -> str:
    if not positions:
        return "현재 열린 포지션 없음"
    lines = []
    for p in positions:
        lines.append(
            f"- {p['direction'].upper()} | entry={p['entry_price']:,.1f} "
            f"SL={p['sl_price']:,.1f} TP={p['tp_price']:,.1f} | "
            f"size={p['size_usdt']}USDT lev={p['leverage']}x | "
            f"regime={p['regime']} | id={p['order_id']}"
        )
    return "\n".join(lines)


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


# ---------------------------------------------------------------------------
# AI Entry Decision
# ---------------------------------------------------------------------------

_ENTRY_PROMPT = """\
You are an aggressive expert BTC/USDT futures trading AI. Decide DIRECTION and CONFIDENCE only.
Leverage, position size, and final order approval are handled by the Risk Engine.

## Multi-Timeframe Technical Analysis
{multi_tf}

## Macro & Sentiment
- Risk regime: {risk}
- Risk summary: {risk_summary}

## Current Positions
{positions}

## Past Trade Performance (last {trade_count} trades)
Win rate: {win_rate}% | Total PnL: {total_pnl} USDT
Direction stats: {direction_stats}
Regime stats: {regime_stats}
Recent 5 trades: {recent_5}

## Rule-Based System Suggestion
- Signal: {rule_signal}
- Regime: {regime}
- Trend/Range: {trend_range}

## Current Price: ${current_price:,.1f}
## ATR (15m): {atr:.1f}

## Decision Guidelines
1. **MULTI-TIMEFRAME ALIGNMENT** is critical:
   - Strong entry: 3+ timeframes agree on direction
   - Moderate: 2 timeframes agree
   - Hold: timeframes disagree or unclear
2. **LEARN FROM PAST**: If a direction/regime consistently loses, avoid it
3. **RISK AWARENESS**: Consider current positions & exposure
4. **CONFIDENCE**: Only recommend entry if genuinely confident (>= 70)
5. **MDD FOCUS**: Prioritize capital preservation over aggressive entries

## Output (strict JSON only, no markdown)
{{"action": "enter_long" | "enter_short" | "hold", "confidence": <0-100>, "reason": "<2-3 sentences in Korean>"}}
"""


def ai_decide_entry(
    multi_tf: dict,
    risk: str,
    risk_summary: str,
    positions: list,
    trade_history: dict,
    rule_signal: str,
    regime: str,
    trend_range: str,
    current_price: float,
    atr: float,
) -> dict:
    """AI 방향/신뢰도 판단. 실패 시 룰 기반 폴백.

    Returns {"action", "confidence", "reason"}.
    Size/leverage는 Risk Engine이 별도 결정.
    """
    try:
        prompt = _ENTRY_PROMPT.format(
            multi_tf=_format_multi_tf(multi_tf),
            risk=risk,
            risk_summary=risk_summary,
            positions=_format_positions(positions),
            trade_count=trade_history.get("total", 0),
            win_rate=trade_history.get("win_rate", 0),
            total_pnl=trade_history.get("total_pnl", 0),
            direction_stats=json.dumps(trade_history.get("direction_stats", {}), ensure_ascii=False),
            regime_stats=json.dumps(trade_history.get("regime_stats", {}), ensure_ascii=False),
            recent_5=json.dumps(trade_history.get("recent_5", []), ensure_ascii=False),
            rule_signal=rule_signal,
            regime=regime,
            trend_range=trend_range,
            current_price=current_price,
            atr=atr,
        )
        parsed = _parse_json_response(
            get_model("gemini-2.5-flash").generate_content(prompt).text
        )

        action = str(parsed.get("action", "hold"))
        if action not in ("enter_long", "enter_short", "hold"):
            action = "hold"
        confidence = min(100, max(0, int(parsed.get("confidence", 0))))
        reason = str(parsed.get("reason", ""))

        print(f"[ai_brain] 진입 판단: 방향={action} 신뢰도={confidence} — {reason}")
        return {"action": action, "confidence": confidence, "reason": reason}

    except Exception as e:
        print(f"[ai_brain] 진입 판단 에러 발생: {e} — 룰 기반(Rule-based) 차트 신호로 대체")
        if rule_signal in ("long", "short"):
            return {
                "action": f"enter_{rule_signal}",
                "confidence": 60,
                "reason": "AI 판단 실패, 룰 기반 폴백",
            }
        return {"action": "hold", "confidence": 0, "reason": "AI 판단 실패"}


# ---------------------------------------------------------------------------
# AI Position Management
# ---------------------------------------------------------------------------

_POSITION_MGMT_PROMPT = """\
You are aggressive managing open BTC/USDT futures positions. Decide on each position.

## Open Positions
{positions}

## Multi-Timeframe Technical Analysis
{multi_tf}

## Current Price: ${current_price:,.1f}
## ATR (15m): {atr:.1f}

## Past Performance
Win rate: {win_rate}% | Recent: {recent_pattern}

## Per-Position Decision Options
1. **hold** — Keep current TP/SL, no changes
2. **close** — Close immediately at market (specify reason)

## Guidelines
- Higher timeframes (4h, 1d) turning against position → close
- Profitable + momentum fading → close to lock profits
- All timeframes still support direction → hold
- Holding a losing position hoping for reversal is the #1 mistake — be decisive

## Output (strict JSON only, no markdown)
{{"positions": [{{"order_id": "<id>", "action": "hold" | "close", "reason": "<one sentence in Korean>"}}]}}
"""


def ai_manage_positions(
    positions: list,
    multi_tf: dict,
    current_price: float,
    atr: float,
    trade_history: dict,
) -> list[dict]:
    """AI 포지션 관리 판단. 실패 시 전체 hold.

    Returns list of {"order_id", "action", "new_sl", "reason"} per position.
    """
    if not positions:
        return []

    try:
        recent_5 = trade_history.get("recent_5", [])
        recent_pattern = ", ".join(
            f"{t['direction']} {t['close_reason']} {t['pnl']:+.1f}"
            for t in recent_5
        ) if recent_5 else "이력 없음"

        prompt = _POSITION_MGMT_PROMPT.format(
            positions=_format_positions(positions),
            multi_tf=_format_multi_tf(multi_tf),
            current_price=current_price,
            atr=atr,
            win_rate=trade_history.get("win_rate", 0),
            recent_pattern=recent_pattern,
        )
        parsed = _parse_json_response(
            get_model("gemini-2.5-flash").generate_content(prompt).text
        )

        actions = parsed.get("positions", [])
        print(f"[ai_brain] 포지션 관리: 총 {len(actions)}건의 판단 완료")
        for a in actions:
            print(f"  -> 주문={a.get('order_id', '?')} | 액션={a.get('action', '?')} | 사유={a.get('reason', '')}")
        return actions

    except Exception as e:
        print(f"[ai_brain] 포지션 관리 에러 발생: {e} — 전량 유지(Hold) 처리")
        return [{"order_id": p["order_id"], "action": "hold", "reason": "AI 실패"} for p in positions]
