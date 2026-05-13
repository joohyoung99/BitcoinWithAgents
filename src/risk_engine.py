"""Risk Engine — 최종 주문 승인 및 리스크 관리.

AI는 direction/confidence만 결정.
레버리지·사이즈·최종 진입 승인은 이 모듈이 전담.

목표: MDD 관리 + 장기 생존성 (수익 극대화보다 안정화)
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Leverage caps (isolated only)
# ---------------------------------------------------------------------------

_MAX_LEVERAGE = {
    ("normal", "long"): 5,
    ("normal", "short"): 3,
    ("caution", "long"): 3,
    ("caution", "short"): 2,
    ("risk_off_trend", "short"): 2,
}


def cap_leverage(regime: str, direction: str) -> int:
    """Return leverage cap for regime + direction. Always isolated margin."""
    return _MAX_LEVERAGE.get((regime, direction), 1)


# ---------------------------------------------------------------------------
# Position sizing (ATR-adjusted)
# ---------------------------------------------------------------------------

_SIZE_MIN = 500.0
_SIZE_MAX = 3000.0


def calc_position_size(balance: float, atr: float, avg_atr: float) -> float:
    """ATR-based position sizing. Higher volatility → smaller size.

    Base: 10% of balance, clamped to 500~1500 USDT.
    If current ATR > historical avg ATR, reduce proportionally (up to -50%).
    """
    base = balance * 0.10
    base = max(_SIZE_MIN, min(base, _SIZE_MAX))

    # ATR reduction: volatile → shrink
    if avg_atr > 0 and atr > avg_atr:
        ratio = avg_atr / atr  # < 1 when more volatile than average
        base *= max(ratio, 0.5)  # reduce up to 50%

    return round(max(_SIZE_MIN, min(base, _SIZE_MAX)), 2)


def check_max_exposure(positions: list, new_size: float, new_lev: int, balance: float) -> tuple[bool, str]:
    """Max total notional exposure cap: 100% of balance."""
    current_notional = sum(
        p.get("size_usdt", 0) * p.get("leverage", 1) for p in positions
    )
    new_notional = new_size * new_lev
    max_notional = balance * 1.0

    if current_notional + new_notional > max_notional:
        return False, f"exposure_cap ({current_notional + new_notional:.0f}/{max_notional:.0f})"
    return True, ""


# ---------------------------------------------------------------------------
# Enhanced risk checks
# ---------------------------------------------------------------------------

INITIAL_BALANCE = 19_293.0  # sync with executor
DAILY_LOSS_PCT = -0.05  # -5%
CONSECUTIVE_LOSS_COOLDOWN = 4
CONSECUTIVE_LOSS_COOLDOWN_HOURS = 2.0
POST_SL_BLOCK_MIN = 30  # same-direction block after SL


def check_daily_loss_pct(daily: dict) -> tuple[bool, str]:
    """Stop trading if daily loss reaches -5% of initial balance."""
    daily_pnl = daily.get("daily_pnl_usdt", 0.0)
    threshold = INITIAL_BALANCE * DAILY_LOSS_PCT
    if daily_pnl <= threshold:
        return False, f"daily_loss_{DAILY_LOSS_PCT:.0%} ({daily_pnl:.0f}/{threshold:.0f})"
    return True, ""


def check_consecutive_loss_cooldown(daily: dict, positions_data: dict) -> tuple[bool, str]:
    """4 consecutive losses → 2h cooldown from last entry."""
    if daily.get("consecutive_losses", 0) >= CONSECUTIVE_LOSS_COOLDOWN:
        last_entry = positions_data.get("last_entry_at", "")
        if last_entry:
            try:
                elapsed_h = (datetime.now(UTC) - datetime.fromisoformat(last_entry)).total_seconds() / 3600
                if elapsed_h < CONSECUTIVE_LOSS_COOLDOWN_HOURS:
                    return False, f"{CONSECUTIVE_LOSS_COOLDOWN}-loss cooldown ({elapsed_h:.1f}/{CONSECUTIVE_LOSS_COOLDOWN_HOURS}h)"
            except Exception:
                pass
        return False, f"{CONSECUTIVE_LOSS_COOLDOWN}-loss cooldown"
    return True, ""


def check_post_sl_reentry(positions_data: dict) -> tuple[bool, str]:
    """Block ANY reentry within 30min after SL."""
    last_sl_at = positions_data.get("last_sl_at", "")
    if last_sl_at:
        try:
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(last_sl_at)).total_seconds() / 60
            if elapsed < POST_SL_BLOCK_MIN:
                return False, f"post-SL block ({elapsed:.0f}/{POST_SL_BLOCK_MIN}min)"
        except Exception:
            pass
    return True, ""


# ---------------------------------------------------------------------------
# Multi-timeframe trend filter
# ---------------------------------------------------------------------------

def check_trend_filter(multi_tf: dict, direction: str) -> tuple[bool, str]:
    """Block entries against BOTH 1h AND 4h trend."""
    against = 0
    for tf in ["1h", "4h"]:
        data = multi_tf.get(tf)
        if not data:
            continue
        trend = data.get("trend", "")
        if direction == "long" and trend == "down":
            against += 1
        elif direction == "short" and trend == "up":
            against += 1

    if against >= 2:
        return False, f"counter-trend: 1h+4h against {direction}"
    return True, ""


# ---------------------------------------------------------------------------
# Economic event filter
# ---------------------------------------------------------------------------

# FOMC 2025-2026 meeting end dates (block day-of and day-before)
_FOMC_DATES = [
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def _is_first_friday(d: datetime) -> bool:
    """NFP: released first Friday of the month."""
    return d.weekday() == 4 and d.day <= 7


def _is_cpi_window(d: datetime) -> bool:
    """CPI: typically released 10th~14th of the month."""
    return 10 <= d.day <= 14


def check_event_filter() -> tuple[bool, str]:
    """Block new entries around CPI / FOMC / NFP."""
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    # FOMC: disabled — AI 판단에 위임
    # for fomc in _FOMC_DATES:
    #     if today == fomc or tomorrow == fomc:
    #         return False, f"FOMC event ({fomc})"

    # NFP: disabled — AI 판단에 위임
    # if _is_first_friday(now):
    #     return False, "NFP (first Friday)"

    # CPI: disabled — 5일 연속 차단이 너무 보수적
    # if _is_cpi_window(now):
    #     return False, f"CPI window (day {now.day})"

    # Custom events file (optional override)
    events_path = Path("data/events.json")
    if events_path.exists():
        try:
            events = json.loads(events_path.read_text(encoding="utf-8"))
            for ev in events.get("blocked_dates", []):
                if today == ev.get("date", ""):
                    return False, f"event: {ev.get('name', 'unknown')}"
        except Exception:
            pass

    return True, ""


# ---------------------------------------------------------------------------
# Unified risk gate
# ---------------------------------------------------------------------------

def run_risk_checks(
    positions: list,
    daily: dict,
    positions_data: dict,
    direction: str,
    new_size: float,
    new_leverage: int,
    balance: float,
    multi_tf: dict,
) -> tuple[bool, str]:
    """Run ALL risk checks. Returns (ok, reason)."""

    checks = [
        check_daily_loss_pct(daily),
        check_consecutive_loss_cooldown(daily, positions_data),
        # check_post_sl_reentry(positions_data),  # Removed per user request: let AI decide
        check_trend_filter(multi_tf, direction),
        check_event_filter(),
        check_max_exposure(positions, new_size, new_leverage, balance),
    ]

    for ok, reason in checks:
        if not ok:
            return False, reason

    return True, ""
