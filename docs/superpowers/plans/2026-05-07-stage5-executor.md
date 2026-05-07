# Stage 5: Executor + WebSocket Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/executor.py` and `src/ws_monitor.py` — deterministic order execution that reads regime signals from `data/state.json`, manages positions via REST (Limit entry + Exchange SL) and WebSocket (fill events + position updates), and enforces 4-layer risk checks on every cycle.

**Architecture:** `executor.py` owns all REST calls and risk logic; `ws_monitor.py` runs as a daemon thread sharing `fill_events`/`fill_results` dicts with executor. Exchange handles SL as safety net; app handles TP and regime-change close. `run_executor_once()` is called serially after `run_regime_once()` in the 15-min scheduler job.

**Tech Stack:** `bitget-python` (REST + WebSocket via `bitpy`), `threading`, `csv`, `json`, `pathlib`, `python-dotenv` (existing)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/explorer.py` | Modify | Fix SoSoValue `collect_etf()` bug |
| `src/executor.py` | Create | Data helpers + risk + order functions + `run_executor_once()` |
| `src/ws_monitor.py` | Create | WebSocket thread, fill events, position close handler |
| `src/explorer.py` | Modify | Wire WS thread + rename scheduler job |
| `tests/test_executor.py` | Create | 11 unit tests |
| `tests/test_ws_monitor.py` | Create | 2 unit tests |

---

## Task 1: Fix SoSoValue collect_etf bug

**Files:**
- Modify: `src/explorer.py` (line 162)
- Test: `tests/test_explorer.py` — verify pre-existing failure now passes

- [ ] **Step 1: Run the failing test to confirm current state**

```
uv run pytest tests/test_explorer.py::test_collect_etf_returns_data -v
```

Expected: FAIL — `assert False` (ETFData is None)

- [ ] **Step 2: Fix collect_etf() in src/explorer.py**

Find the line:
```python
flows = resp.json()["data"][:3]
```

Replace with:
```python
flows = resp.json()[:3]
```

The SoSoValue API returns a list directly, not `{"data": [...]}`.

- [ ] **Step 3: Run test to verify it passes**

```
uv run pytest tests/test_explorer.py::test_collect_etf_returns_data -v
```

Expected: PASS

- [ ] **Step 4: Run full suite to confirm no regressions**

```
uv run pytest -v
```

Expected: all 47+ tests pass (0 failures now)

- [ ] **Step 5: Commit**

```
git add src/explorer.py
git commit -m "fix: collect_etf SoSoValue response is list not dict"
```

---

## Task 2: executor.py skeleton + data file helpers

**Files:**
- Create: `src/executor.py`
- Create: `tests/test_executor.py` (skeleton only)

Data files live in `data/`. Helper functions read/write `positions.json`, `daily.json`, and append `trades.csv`.

- [ ] **Step 1: Write failing test**

Create `tests/test_executor.py`:

```python
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


def test_load_save_positions(tmp_path, monkeypatch):
    import src.executor as ex
    monkeypatch.setattr(ex, "POSITIONS_PATH", tmp_path / "positions.json")

    data = ex.load_positions()
    assert data == {"last_transition": "", "positions": []}

    data["positions"].append({"order_id": "1"})
    ex.save_positions(data)

    reloaded = ex.load_positions()
    assert reloaded["positions"][0]["order_id"] == "1"


def test_load_save_daily_resets_on_new_date(tmp_path, monkeypatch):
    import src.executor as ex
    monkeypatch.setattr(ex, "DAILY_PATH", tmp_path / "daily.json")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    yesterday_data = {
        "date": "2000-01-01",
        "trade_count": 5,
        "wins": 3,
        "losses": 2,
        "consecutive_losses": 2,
        "daily_pnl_usdt": -200.0,
    }
    (tmp_path / "daily.json").write_text(json.dumps(yesterday_data), encoding="utf-8")

    daily = ex.load_daily()
    assert daily["date"] == today
    assert daily["trade_count"] == 0
    assert daily["consecutive_losses"] == 0
```

- [ ] **Step 2: Run to verify it fails**

```
uv run pytest tests/test_executor.py::test_load_save_positions tests/test_executor.py::test_load_save_daily_resets_on_new_date -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.executor'`

- [ ] **Step 3: Create src/executor.py**

```python
from __future__ import annotations

import csv
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.client import get_client

STATE_PATH = Path("data/state.json")
POSITIONS_PATH = Path("data/positions.json")
DAILY_PATH = Path("data/daily.json")
TRADES_PATH = Path("data/trades.csv")

TRADES_HEADER = [
    "opened_at", "closed_at", "direction", "regime", "leverage",
    "entry_price", "close_price", "size_usdt", "sl_price", "tp_price",
    "realized_pnl_usdt", "close_reason", "atr_at_entry",
]

_DAILY_TEMPLATE = {
    "trade_count": 0,
    "wins": 0,
    "losses": 0,
    "consecutive_losses": 0,
    "daily_pnl_usdt": 0.0,
}


def load_positions() -> dict:
    if POSITIONS_PATH.exists():
        try:
            return json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_transition": "", "positions": []}


def save_positions(data: dict) -> None:
    POSITIONS_PATH.parent.mkdir(exist_ok=True)
    tmp = POSITIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(POSITIONS_PATH))


def load_daily() -> dict:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if DAILY_PATH.exists():
        try:
            data = json.loads(DAILY_PATH.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data
        except Exception:
            pass
    fresh = {"date": today, **_DAILY_TEMPLATE}
    save_daily(fresh)
    return fresh


def save_daily(data: dict) -> None:
    DAILY_PATH.parent.mkdir(exist_ok=True)
    tmp = DAILY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(DAILY_PATH))


def append_trade(row: dict) -> None:
    TRADES_PATH.parent.mkdir(exist_ok=True)
    write_header = not TRADES_PATH.exists()
    with TRADES_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in TRADES_HEADER})
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_executor.py::test_load_save_positions tests/test_executor.py::test_load_save_daily_resets_on_new_date -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/executor.py tests/test_executor.py
git commit -m "feat: executor skeleton with data file helpers"
```

---

## Task 3: Pure calculation functions

**Files:**
- Modify: `src/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_executor.py`:

```python
def test_calc_trade_size():
    from src.executor import calc_trade_size
    assert calc_trade_size(20000.0) == 2000.0
    assert calc_trade_size(5000.0) == 500.0
    assert calc_trade_size(1000.0) == pytest.approx(100.0)
    assert calc_trade_size(100.0) == pytest.approx(10.0)


def test_check_risk_halt_regime():
    from src.executor import check_risk
    ok, reason = check_risk([], {"daily_pnl_usdt": 0.0, "consecutive_losses": 0}, "halt")
    assert ok is False
    assert reason == "halt"


def test_check_risk_max_positions():
    from src.executor import check_risk
    positions = [{"order_id": "1"}, {"order_id": "2"}]
    ok, reason = check_risk(positions, {"daily_pnl_usdt": 0.0, "consecutive_losses": 0}, "normal")
    assert ok is False
    assert reason == "max_positions"


def test_check_risk_daily_loss():
    from src.executor import check_risk
    ok, reason = check_risk([], {"daily_pnl_usdt": -1500.0, "consecutive_losses": 0}, "normal")
    assert ok is False
    assert reason == "daily_loss"


def test_check_risk_consecutive_losses():
    from src.executor import check_risk
    ok, reason = check_risk([], {"daily_pnl_usdt": 0.0, "consecutive_losses": 3}, "normal")
    assert ok is False
    assert reason == "consecutive"


def test_check_risk_passes():
    from src.executor import check_risk
    ok, reason = check_risk([], {"daily_pnl_usdt": 100.0, "consecutive_losses": 0}, "normal")
    assert ok is True
    assert reason == ""


def test_calc_sl_tp_normal_long():
    from src.executor import calc_sl_price, calc_tp_price
    # Normal: SL = ATR×1.5, TP = ATR×3.0
    assert calc_sl_price("long", 50000.0, 1000.0, "normal") == pytest.approx(48500.0)
    assert calc_tp_price("long", 50000.0, 1000.0, "normal") == pytest.approx(53000.0)


def test_calc_sl_tp_normal_short():
    from src.executor import calc_sl_price, calc_tp_price
    assert calc_sl_price("short", 50000.0, 1000.0, "normal") == pytest.approx(51500.0)
    assert calc_tp_price("short", 50000.0, 1000.0, "normal") == pytest.approx(47000.0)


def test_get_leverage():
    from src.executor import get_leverage
    assert get_leverage("normal", "long") == 5
    assert get_leverage("normal", "short") == 3
    assert get_leverage("caution", "long") == 3
    assert get_leverage("caution", "short") == 2
    assert get_leverage("risk_off_trend", "short") == 2
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/test_executor.py -k "calc or risk or leverage" -v
```

Expected: FAIL — ImportError

- [ ] **Step 3: Implement in src/executor.py**

Add after `append_trade`:

```python
_SL_MULT = {"normal": 1.5, "caution": 1.0, "risk_off_trend": 1.0}
_TP_MULT = {"normal": 3.0, "caution": 2.0, "risk_off_trend": 2.0}
_LEVERAGE = {
    ("normal", "long"): 5,
    ("normal", "short"): 3,
    ("caution", "long"): 3,
    ("caution", "short"): 2,
    ("risk_off_trend", "short"): 2,
}

MAX_POSITIONS = 2
DAILY_LOSS_LIMIT = -1500.0
MAX_CONSECUTIVE_LOSSES = 3


def calc_trade_size(balance: float) -> float:
    return min(2000.0, balance * 0.10)


def check_risk(positions: list, daily: dict, regime: str) -> tuple[bool, str]:
    if regime == "halt":
        return False, "halt"
    if len(positions) >= MAX_POSITIONS:
        return False, "max_positions"
    if daily.get("daily_pnl_usdt", 0.0) <= DAILY_LOSS_LIMIT:
        return False, "daily_loss"
    if daily.get("consecutive_losses", 0) >= MAX_CONSECUTIVE_LOSSES:
        return False, "consecutive"
    return True, ""


def get_leverage(regime: str, direction: str) -> int:
    return _LEVERAGE.get((regime, direction), 1)


def calc_sl_price(direction: str, entry: float, atr: float, regime: str) -> float:
    mult = _SL_MULT.get(regime, 1.0)
    return entry - atr * mult if direction == "long" else entry + atr * mult


def calc_tp_price(direction: str, entry: float, atr: float, regime: str) -> float:
    mult = _TP_MULT.get(regime, 2.0)
    return entry + atr * mult if direction == "long" else entry - atr * mult
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_executor.py -k "calc or risk or leverage" -v
```

Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```
git add src/executor.py tests/test_executor.py
git commit -m "feat: executor calc_trade_size, check_risk, calc_sl_tp, get_leverage"
```

---

## Task 4: Bitget order functions

**Files:**
- Modify: `src/executor.py`
- Modify: `tests/test_executor.py`

Bitget mix futures REST calls via `bitpy`. All functions return `dict` on success or `None` on failure. All have 3-retry loops with 1s sleep between attempts.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_executor.py`:

```python
def test_place_entry_retry_succeeds_on_third():
    from src.executor import place_limit_order

    mock_client = MagicMock()
    fail = MagicMock()
    fail.code = "50001"
    fail.msg = "error"
    succeed = MagicMock()
    succeed.code = "00000"
    succeed.data = {"orderId": "abc123", "size": "0.02"}
    mock_client.mix.place_order.side_effect = [fail, fail, succeed]

    with patch("src.executor.time.sleep"):
        result = place_limit_order(mock_client, "long", 1000.0, 50000.0, 5)

    assert result is not None
    assert result["orderId"] == "abc123"
    assert mock_client.mix.place_order.call_count == 3


def test_place_entry_all_retries_fail():
    from src.executor import place_limit_order

    mock_client = MagicMock()
    fail = MagicMock()
    fail.code = "50001"
    mock_client.mix.place_order.side_effect = [fail, fail, fail]

    with patch("src.executor.time.sleep"):
        result = place_limit_order(mock_client, "long", 1000.0, 50000.0, 5)

    assert result is None


def test_sl_failure_triggers_emergency_close():
    from src.executor import place_stop_loss_with_emergency

    mock_client = MagicMock()
    fail = MagicMock()
    fail.code = "50001"
    mock_client.mix.place_tpsl_order.side_effect = [fail, fail, fail]

    ok_close = MagicMock()
    ok_close.code = "00000"
    mock_client.mix.flash_close_positions.return_value = ok_close

    position = {
        "order_id": "abc",
        "direction": "long",
        "size_usdt": 1000.0,
        "entry_price": 50000.0,
        "sl_price": 48500.0,
        "tp_price": 53000.0,
        "sl_order_id": "",
        "regime": "normal",
        "leverage": 5,
        "atr_at_entry": 1000.0,
        "opened_at": "2026-05-07T00:00:00+00:00",
    }

    with patch("src.executor.time.sleep"):
        result = place_stop_loss_with_emergency(mock_client, position)

    assert result is False
    mock_client.mix.flash_close_positions.assert_called_once()
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/test_executor.py -k "entry or sl_failure" -v
```

Expected: FAIL — ImportError

- [ ] **Step 3: Implement order functions in src/executor.py**

Add after the calculation functions:

```python
SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"


def _qty_from_usdt(size_usdt: float, price: float, leverage: int) -> str:
    """Convert USDT notional to BTC contract quantity (rounded to 3dp)."""
    qty = (size_usdt * leverage) / price
    return f"{qty:.3f}"


def place_limit_order(client, direction: str, size_usdt: float, price: float, leverage: int) -> dict | None:
    side = "buy" if direction == "long" else "sell"
    qty = _qty_from_usdt(size_usdt, price, leverage)
    for attempt in range(3):
        try:
            resp = client.mix.place_order(
                symbol=SYMBOL,
                productType=PRODUCT_TYPE,
                marginMode="isolated",
                marginCoin=MARGIN_COIN,
                size=qty,
                price=str(price),
                side=side,
                orderType="limit",
                leverage=str(leverage),
            )
            if resp.code == "00000":
                return resp.data
            print(f"[executor] place_order attempt {attempt+1} failed: {resp.code} {resp.msg}")
        except Exception as e:
            print(f"[executor] place_order attempt {attempt+1} exception: {e}")
        if attempt < 2:
            time.sleep(1)
    return None


def cancel_order(client, order_id: str) -> bool:
    try:
        resp = client.mix.cancel_order(
            symbol=SYMBOL,
            productType=PRODUCT_TYPE,
            orderId=order_id,
        )
        return resp.code == "00000"
    except Exception as e:
        print(f"[executor] cancel_order failed: {e}")
        return False


def close_position_market(client, position: dict, reason: str, daily: dict, positions_data: dict) -> bool:
    hold_side = position["direction"]
    try:
        resp = client.mix.flash_close_positions(
            symbol=SYMBOL,
            productType=PRODUCT_TYPE,
            holdSide=hold_side,
        )
        if resp.code != "00000":
            print(f"[executor] flash_close failed: {resp.code} {resp.msg}")
            return False
    except Exception as e:
        print(f"[executor] flash_close exception: {e}")
        return False

    now = datetime.now(UTC).isoformat()
    close_price = position.get("tp_price" if reason == "TP" else "sl_price", 0.0)
    pnl = _calc_pnl(position, close_price)

    append_trade({
        "opened_at": position["opened_at"],
        "closed_at": now,
        "direction": position["direction"],
        "regime": position["regime"],
        "leverage": position["leverage"],
        "entry_price": position["entry_price"],
        "close_price": close_price,
        "size_usdt": position["size_usdt"],
        "sl_price": position["sl_price"],
        "tp_price": position["tp_price"],
        "realized_pnl_usdt": round(pnl, 4),
        "close_reason": reason,
        "atr_at_entry": position["atr_at_entry"],
    })

    daily["daily_pnl_usdt"] = round(daily.get("daily_pnl_usdt", 0.0) + pnl, 4)
    daily["trade_count"] = daily.get("trade_count", 0) + 1
    if pnl >= 0:
        daily["wins"] = daily.get("wins", 0) + 1
        daily["consecutive_losses"] = 0
    else:
        daily["losses"] = daily.get("losses", 0) + 1
        daily["consecutive_losses"] = daily.get("consecutive_losses", 0) + 1

    positions_data["positions"] = [
        p for p in positions_data["positions"] if p["order_id"] != position["order_id"]
    ]
    return True


def _calc_pnl(position: dict, close_price: float) -> float:
    direction = position["direction"]
    entry = position["entry_price"]
    size_usdt = position["size_usdt"]
    leverage = position["leverage"]
    if direction == "long":
        return size_usdt * leverage * (close_price - entry) / entry
    return size_usdt * leverage * (entry - close_price) / entry


def place_stop_loss_with_emergency(client, position: dict) -> bool:
    """Submit exchange-side SL. On 3 failures → emergency market close."""
    hold_side = position["direction"]
    sl_price = position["sl_price"]
    for attempt in range(3):
        try:
            resp = client.mix.place_tpsl_order(
                symbol=SYMBOL,
                productType=PRODUCT_TYPE,
                marginCoin=MARGIN_COIN,
                planType="loss_plan",
                triggerPrice=str(sl_price),
                holdSide=hold_side,
                size="0",
                triggerType="mark_price",
            )
            if resp.code == "00000":
                return True
            print(f"[executor] SL attempt {attempt+1} failed: {resp.code}")
        except Exception as e:
            print(f"[executor] SL attempt {attempt+1} exception: {e}")
        if attempt < 2:
            time.sleep(1)

    print("[executor] SL all retries failed — emergency market close")
    try:
        client.mix.flash_close_positions(
            symbol=SYMBOL,
            productType=PRODUCT_TYPE,
            holdSide=hold_side,
        )
    except Exception as e:
        print(f"[executor] emergency close failed: {e}")
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_executor.py -k "entry or sl_failure" -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```
git add src/executor.py tests/test_executor.py
git commit -m "feat: executor place_limit_order, place_stop_loss, close_position_market"
```

---

## Task 5: check_tp_hits + handle_regime_change

**Files:**
- Modify: `src/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_executor.py`:

```python
def test_tp_hit_triggers_market_close(tmp_path, monkeypatch):
    import src.executor as ex
    from src.executor import check_tp_hits
    monkeypatch.setattr(ex, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(ex, "DAILY_PATH", tmp_path / "daily.json")
    monkeypatch.setattr(ex, "TRADES_PATH", tmp_path / "trades.csv")

    position = {
        "order_id": "tp1",
        "direction": "long",
        "size_usdt": 1000.0,
        "entry_price": 50000.0,
        "sl_price": 48500.0,
        "tp_price": 53000.0,
        "sl_order_id": "sl1",
        "regime": "normal",
        "leverage": 5,
        "atr_at_entry": 1000.0,
        "opened_at": "2026-05-07T00:00:00+00:00",
    }
    positions_data = {"last_transition": "", "positions": [position]}
    daily = {"date": "2026-05-07", "trade_count": 0, "wins": 0,
             "losses": 0, "consecutive_losses": 0, "daily_pnl_usdt": 0.0}

    mock_client = MagicMock()
    close_resp = MagicMock()
    close_resp.code = "00000"
    mock_client.mix.flash_close_positions.return_value = close_resp

    # current_price 53001 >= tp_price 53000 → TP hit
    check_tp_hits(mock_client, positions_data, daily, current_price=53001.0)

    mock_client.mix.flash_close_positions.assert_called_once()
    assert len(positions_data["positions"]) == 0
    assert daily["wins"] == 1


def test_handle_regime_change_idempotent(tmp_path, monkeypatch):
    import src.executor as ex
    from src.executor import handle_regime_change
    monkeypatch.setattr(ex, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(ex, "DAILY_PATH", tmp_path / "daily.json")
    monkeypatch.setattr(ex, "TRADES_PATH", tmp_path / "trades.csv")

    positions_data = {"last_transition": "NORMAL_TO_HALT", "positions": []}
    daily = {"date": "2026-05-07", "trade_count": 0, "wins": 0,
             "losses": 0, "consecutive_losses": 0, "daily_pnl_usdt": 0.0}

    mock_client = MagicMock()
    # Same transition already handled → no Bitget calls
    handle_regime_change(mock_client, positions_data, daily,
                         new_regime="halt", prev_regime="normal",
                         atr=1000.0, current_price=50000.0)

    mock_client.mix.flash_close_positions.assert_not_called()
    mock_client.mix.place_tpsl_order.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/test_executor.py -k "tp_hit or idempotent" -v
```

Expected: FAIL — ImportError

- [ ] **Step 3: Implement check_tp_hits + handle_regime_change in src/executor.py**

Add after `place_stop_loss_with_emergency`:

```python
def check_tp_hits(client, positions_data: dict, daily: dict, current_price: float) -> None:
    for pos in list(positions_data["positions"]):
        tp = pos["tp_price"]
        direction = pos["direction"]
        hit = current_price >= tp if direction == "long" else current_price <= tp
        if hit:
            print(f"[executor] TP hit order={pos['order_id']} price={current_price} tp={tp}")
            close_position_market(client, pos, "TP", daily, positions_data)


def _tighten_sl(client, pos: dict, atr: float) -> None:
    new_sl = calc_sl_price(pos["direction"], pos["entry_price"], atr, "tight")
    # tight uses 0.8 multiplier
    mult = 0.8
    entry = pos["entry_price"]
    new_sl = entry - atr * mult if pos["direction"] == "long" else entry + atr * mult

    for attempt in range(3):
        try:
            # Cancel old SL first if exists
            if pos.get("sl_order_id"):
                cancel_order(client, pos["sl_order_id"])
            resp = client.mix.place_tpsl_order(
                symbol=SYMBOL,
                productType=PRODUCT_TYPE,
                marginCoin=MARGIN_COIN,
                planType="loss_plan",
                triggerPrice=str(round(new_sl, 2)),
                holdSide=pos["direction"],
                size="0",
                triggerType="mark_price",
            )
            if resp.code == "00000":
                pos["sl_price"] = new_sl
                pos["sl_order_id"] = resp.data.get("orderId", "")
                return
        except Exception as e:
            print(f"[executor] tighten_sl attempt {attempt+1}: {e}")
        if attempt < 2:
            time.sleep(1)
    print(f"[executor] tighten_sl failed — keeping old SL order={pos['order_id']}")


def handle_regime_change(
    client,
    positions_data: dict,
    daily: dict,
    new_regime: str,
    prev_regime: str,
    atr: float,
    current_price: float,
) -> None:
    transition = f"{prev_regime.upper()}_TO_{new_regime.upper()}"

    if positions_data.get("last_transition") == transition:
        print(f"[executor] regime change {transition} already handled — skip")
        return

    halt_transitions = {
        "NORMAL_TO_HALT", "CAUTION_TO_HALT",
        "RISK_OFF_TREND_TO_HALT", "NORMAL_TO_RISK_OFF_TREND",
    }
    tighten_transitions = {"NORMAL_TO_CAUTION"}

    if transition in halt_transitions:
        for pos in list(positions_data["positions"]):
            pnl = _calc_pnl(pos, current_price)
            if pnl > 0:
                print(f"[executor] {transition}: closing profitable pos {pos['order_id']}")
                close_position_market(client, pos, "REGIME_CHANGE", daily, positions_data)
            else:
                print(f"[executor] {transition}: tightening SL pos {pos['order_id']}")
                _tighten_sl(client, pos, atr)

    elif transition in tighten_transitions:
        for pos in positions_data["positions"]:
            _tighten_sl(client, pos, atr)

    positions_data["last_transition"] = transition
    save_positions(positions_data)
    print(f"[executor] regime change handled: {transition}")
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_executor.py -k "tp_hit or idempotent" -v
```

Expected: PASS

- [ ] **Step 5: Run all executor tests**

```
uv run pytest tests/test_executor.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```
git add src/executor.py tests/test_executor.py
git commit -m "feat: executor check_tp_hits + handle_regime_change"
```

---

## Task 6: ws_monitor.py

**Files:**
- Create: `src/ws_monitor.py`
- Create: `tests/test_ws_monitor.py`

WebSocket monitoring thread. Shares `fill_events` and `fill_results` dicts with executor.

- [ ] **Step 1: Check available WebSocket client**

```
uv run python -c "import bitpy; print([x for x in dir(bitpy) if 'ws' in x.lower() or 'Ws' in x])"
uv run python -c "from bitpy.ws_client import BitgetWsClient; print('ok')"
```

If the second command fails, try:
```
uv run python -c "from pybitget.stream import BitgetWsClient; print('ok')"
```

Use whichever import path works.

- [ ] **Step 2: Write failing tests**

Create `tests/test_ws_monitor.py`:

```python
import threading
import time
from unittest.mock import MagicMock, patch


def test_fill_event_set_by_order_id():
    import src.ws_monitor as ws
    # Reset shared state
    ws.fill_events.clear()
    ws.fill_results.clear()

    order_id = "test_order_123"
    event = threading.Event()
    ws.fill_events[order_id] = event

    # Simulate ws_monitor receiving a fill message
    ws.handle_order_event({
        "ordId": order_id,
        "status": "full_fill",
        "avgPx": "50000",
        "accFillSz": "0.02",
    })

    assert event.is_set()
    assert ws.fill_results[order_id]["avgPx"] == "50000"


def test_fill_event_cleanup_on_timeout():
    import src.ws_monitor as ws
    ws.fill_events.clear()
    ws.fill_results.clear()

    order_id = "timeout_order"
    ws.fill_events[order_id] = threading.Event()
    ws.fill_results[order_id] = {"avgPx": "50000"}

    # Simulate finally-block cleanup (what executor does)
    try:
        filled = ws.fill_events[order_id].wait(timeout=0.01)
    finally:
        ws.fill_events.pop(order_id, None)
        ws.fill_results.pop(order_id, None)

    assert order_id not in ws.fill_events
    assert order_id not in ws.fill_results
```

- [ ] **Step 3: Run to verify they fail**

```
uv run pytest tests/test_ws_monitor.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.ws_monitor'`

- [ ] **Step 4: Create src/ws_monitor.py**

```python
from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Shared state with executor — keyed by order_id
fill_events: dict[str, threading.Event] = {}
fill_results: dict[str, dict] = {}

POSITIONS_PATH = Path("data/positions.json")
DAILY_PATH = Path("data/daily.json")
TRADES_PATH = Path("data/trades.csv")

TRADES_HEADER = [
    "opened_at", "closed_at", "direction", "regime", "leverage",
    "entry_price", "close_price", "size_usdt", "sl_price", "tp_price",
    "realized_pnl_usdt", "close_reason", "atr_at_entry",
]

_MAX_RECONNECT = 5
_RECONNECT_DELAY = 5


def handle_order_event(data: dict) -> None:
    order_id = data.get("ordId", "")
    status = data.get("status", "")
    if status in ("full_fill", "partial_fill") and order_id in fill_events:
        fill_results[order_id] = data
        fill_events[order_id].set()


def _load_positions() -> dict:
    if POSITIONS_PATH.exists():
        try:
            return json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_transition": "", "positions": []}


def _save_positions(data: dict) -> None:
    import os as _os
    POSITIONS_PATH.parent.mkdir(exist_ok=True)
    tmp = POSITIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _os.replace(str(tmp), str(POSITIONS_PATH))


def handle_position_close_event(data: dict) -> None:
    pos_id = data.get("posId", "")
    close_reason = data.get("closeType", "SL_EXCHANGE")
    close_price = float(data.get("achievedProfits", 0.0))

    positions_data = _load_positions()
    matched = [p for p in positions_data["positions"] if p.get("order_id") == pos_id]
    if not matched:
        return

    pos = matched[0]
    import csv as _csv
    TRADES_PATH.parent.mkdir(exist_ok=True)
    write_header = not TRADES_PATH.exists()
    pnl = float(data.get("achievedProfits", 0.0))
    row = {
        "opened_at": pos.get("opened_at", ""),
        "closed_at": datetime.now(UTC).isoformat(),
        "direction": pos.get("direction", ""),
        "regime": pos.get("regime", ""),
        "leverage": pos.get("leverage", ""),
        "entry_price": pos.get("entry_price", ""),
        "close_price": close_price,
        "size_usdt": pos.get("size_usdt", ""),
        "sl_price": pos.get("sl_price", ""),
        "tp_price": pos.get("tp_price", ""),
        "realized_pnl_usdt": round(pnl, 4),
        "close_reason": "SL_EXCHANGE",
        "atr_at_entry": pos.get("atr_at_entry", ""),
    }
    with TRADES_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=TRADES_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    positions_data["positions"] = [
        p for p in positions_data["positions"] if p.get("order_id") != pos_id
    ]
    _save_positions(positions_data)
    print(f"[ws_monitor] SL_EXCHANGE closed pos={pos_id} pnl={pnl}")


def _on_message(message: str) -> None:
    try:
        data = json.loads(message)
        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        events = data.get("data", [])
        for event in events:
            if channel == "orders":
                handle_order_event(event)
            elif channel == "positions":
                if event.get("closeType"):
                    handle_position_close_event(event)
    except Exception as e:
        print(f"[ws_monitor] message parse error: {e}")


def run_ws_monitor() -> None:
    api_key = os.getenv("BITGET_API_KEY", "")
    api_secret = os.getenv("BITGET_SECRET_KEY", "")
    passphrase = os.getenv("BITGET_PASSPHRASE", "")
    is_demo = os.getenv("BITGET_IS_DEMO", "false").lower() == "true"

    if not all([api_key, api_secret, passphrase]):
        print("[ws_monitor] API keys not set — WebSocket disabled")
        return

    attempts = 0
    while attempts < _MAX_RECONNECT:
        try:
            from bitpy.ws_client import BitgetWsClient
        except ImportError:
            try:
                from pybitget.stream import BitgetWsClient  # type: ignore
            except ImportError:
                print("[ws_monitor] No WebSocket client available — disabled")
                return

        try:
            client = BitgetWsClient(
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
            )
            client.subscribe([
                {"instType": "USDT-FUTURES", "channel": "orders", "instId": "default"},
                {"instType": "USDT-FUTURES", "channel": "positions", "instId": "default"},
            ])
            client.run_forever(on_message=_on_message)
            attempts = 0  # reset on clean disconnect
        except Exception as e:
            attempts += 1
            print(f"[ws_monitor] connection error (attempt {attempts}/{_MAX_RECONNECT}): {e}")
            if attempts < _MAX_RECONNECT:
                time.sleep(_RECONNECT_DELAY)

    print("[ws_monitor] max reconnect attempts reached — WebSocket disabled")
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/test_ws_monitor.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```
git add src/ws_monitor.py tests/test_ws_monitor.py
git commit -m "feat: ws_monitor with fill_events dict and position close handler"
```

---

## Task 7: run_executor_once

**Files:**
- Modify: `src/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_executor.py`:

```python
def test_run_executor_once_skips_on_halt(tmp_path, monkeypatch):
    import src.executor as ex
    from src.executor import run_executor_once

    monkeypatch.setattr(ex, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(ex, "DAILY_PATH", tmp_path / "daily.json")
    monkeypatch.setattr(ex, "TRADES_PATH", tmp_path / "trades.csv")
    monkeypatch.setattr(ex, "STATE_PATH", tmp_path / "state.json")

    state = {
        "meta": {"schema_version": 1, "updated_at": "2026-05-07T12:00:00+00:00"},
        "BTCUSDT": {
            "updated_at": "2026-05-07T12:00:00+00:00",
            "regime": "halt",
            "entry_signal": "long",
            "confidence": 80,
            "atr": 1000.0,
            "regime_changed": False,
            "regime_transition": "",
            "close": 50000.0,
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    mock_client = MagicMock()
    with patch("src.executor.get_client", return_value=mock_client):
        run_executor_once()

    mock_client.mix.place_order.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

```
uv run pytest tests/test_executor.py::test_run_executor_once_skips_on_halt -v
```

Expected: FAIL — ImportError for `run_executor_once`

- [ ] **Step 3: Implement run_executor_once in src/executor.py**

Append at the end of `src/executor.py`:

```python
def run_executor_once() -> None:
    try:
        # 1. Read state.json
        if not STATE_PATH.exists():
            print("[executor] state.json missing — skip")
            return
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        sym = state.get("BTCUSDT", {})
        if not sym:
            print("[executor] no BTCUSDT in state.json — skip")
            return

        regime = sym.get("regime", "halt")
        entry_signal = sym.get("entry_signal", "none")
        atr = float(sym.get("atr", 0.0))
        current_price = float(sym.get("close", 0.0))
        regime_changed = sym.get("regime_changed", False)
        regime_transition = sym.get("regime_transition", "")
        prev_regime = sym.get("prev_regime", regime)

        # 2. Load positions + daily
        positions_data = load_positions()
        daily = load_daily()
        positions = positions_data["positions"]

        client = get_client()

        # 3. TP check first (existing positions take priority)
        if positions and current_price > 0:
            check_tp_hits(client, positions_data, daily, current_price)
            save_positions(positions_data)
            save_daily(daily)

        # 4. Regime change handling
        if regime_changed and regime_transition:
            handle_regime_change(
                client, positions_data, daily,
                new_regime=regime,
                prev_regime=prev_regime,
                atr=atr,
                current_price=current_price,
            )
            save_positions(positions_data)
            save_daily(daily)

        # 5. Risk checks
        ok, reason = check_risk(positions_data["positions"], daily, regime)
        if not ok:
            print(f"[executor] risk check failed: {reason} — skip entry")
            return

        # 6. Entry signal check
        if entry_signal == "none":
            print("[executor] entry_signal=none — skip")
            return

        direction = entry_signal  # "long" | "short"
        if regime == "risk_off_trend" and direction == "long":
            print("[executor] risk_off_trend: long blocked — skip")
            return

        # 7. Position sizing
        balance_data = client.account.get_accounts(PRODUCT_TYPE)
        if balance_data.code != "00000" or not balance_data.data:
            print("[executor] balance fetch failed — skip")
            return
        balance = float(balance_data.data[0]["available"])
        size_usdt = calc_trade_size(balance)
        leverage = get_leverage(regime, direction)

        # 8. Place limit entry (3 retry)
        order_result = place_limit_order(client, direction, size_usdt, current_price, leverage)
        if not order_result:
            print("[executor] entry order failed after retries — skip")
            return
        order_id = str(order_result.get("orderId", ""))

        # 9. Wait for fill via WebSocket event (30s timeout)
        from src.ws_monitor import fill_events, fill_results
        fill_events[order_id] = threading.Event()
        try:
            filled = fill_events[order_id].wait(timeout=30)
            fill_data = fill_results.get(order_id)
        finally:
            fill_events.pop(order_id, None)
            fill_results.pop(order_id, None)

        if not filled:
            print(f"[executor] order {order_id} not filled in 30s — cancelling")
            cancel_order(client, order_id)
            return

        entry_price = float(fill_data.get("avgPx", current_price)) if fill_data else current_price
        sl_price = calc_sl_price(direction, entry_price, atr, regime)
        tp_price = calc_tp_price(direction, entry_price, atr, regime)

        # 10. Place exchange SL (3 retry, emergency close on all fail)
        position_record = {
            "order_id": order_id,
            "direction": direction,
            "size_usdt": size_usdt,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "sl_order_id": "",
            "regime": regime,
            "leverage": leverage,
            "atr_at_entry": atr,
            "opened_at": datetime.now(UTC).isoformat(),
        }
        sl_ok = place_stop_loss_with_emergency(client, position_record)
        if not sl_ok:
            print(f"[executor] SL failed + emergency close triggered for {order_id}")
            return

        # 11. Update positions.json
        positions_data["positions"].append(position_record)
        save_positions(positions_data)
        print(f"[executor] opened {direction} order={order_id} entry={entry_price} sl={sl_price} tp={tp_price}")

    except Exception as e:
        print(f"[executor] run_executor_once error: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_executor.py::test_run_executor_once_skips_on_halt -v
```

Expected: PASS

- [ ] **Step 5: Run all tests**

```
uv run pytest tests/test_executor.py tests/test_ws_monitor.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```
git add src/executor.py tests/test_executor.py
git commit -m "feat: run_executor_once full pipeline"
```

---

## Task 8: explorer.py integration

**Files:**
- Modify: `src/explorer.py` (`main()` only)

Replace `run_chart_and_regime` job with `run_chart_and_regime_and_executor`. Start WebSocket daemon thread.

- [ ] **Step 1: Read current main() content**

```
uv run python -c "import inspect; from src.explorer import main; print(inspect.getsource(main))"
```

Confirm current job id is `"chart_regime"`.

- [ ] **Step 2: Modify src/explorer.py main()**

Replace the entire `main()` function:

```python
def main() -> None:
    import threading
    from apscheduler.schedulers.blocking import BlockingScheduler
    from src.chart import run_chart_once
    from src.executor import run_executor_once
    from src.regime import run_regime_once
    from src.ws_monitor import run_ws_monitor

    def run_chart_and_regime_and_executor() -> None:
        run_chart_once()
        run_regime_once()
        run_executor_once()

    # Start WebSocket monitoring as daemon thread
    ws_thread = threading.Thread(target=run_ws_monitor, daemon=True, name="ws_monitor")
    ws_thread.start()
    print("[explorer] WebSocket monitor started")

    print("[explorer] starting — running once immediately")
    run_once()
    run_chart_and_regime_and_executor()

    now = datetime.now(UTC)
    scheduler = BlockingScheduler()
    scheduler.add_job(run_once, "interval", hours=1, id="explorer")
    scheduler.add_job(
        run_chart_and_regime_and_executor,
        "interval",
        minutes=15,
        start_date=now + timedelta(minutes=5),
        id="chart_regime_executor",
    )
    print("[explorer] scheduler started — explorer 1h, chart+regime+executor 15min (Ctrl+C to stop)")
    scheduler.start()
```

- [ ] **Step 3: Verify all tests pass**

```
uv run pytest -v
```

Expected: all tests pass

- [ ] **Step 4: Verify imports are clean**

```
uv run python -c "from src.executor import run_executor_once; from src.ws_monitor import run_ws_monitor; print('imports ok')"
```

- [ ] **Step 5: Commit**

```
git add src/explorer.py
git commit -m "feat: wire executor + ws_monitor into explorer main()"
```

---

## Post-Implementation Check

```
uv run pytest -v
uv run python -c "from src.executor import run_executor_once; from src.ws_monitor import run_ws_monitor; print('all ok')"
```
