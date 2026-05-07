# Stage 5 Design — 리스크 관리 + 주문 실행 (Executor)

**날짜:** 2026-05-07
**범위:** 5단계 — 조건 체크 + Limit 진입 + Exchange SL + TP/Regime 청산 + WebSocket 상태 동기화
**완료 기준:** regime 신호 기반 자동 주문 실행, SL 거래소 위임, TP/Regime 청산 앱 능동 처리, 포지션 상태 실시간 동기화

---

## 선택한 접근법

**두 파일: `src/executor.py` + `src/ws_monitor.py`**

판단은 상위 단계(chart Gemini confidence → regime Gemini advisory)에서 완료. executor는 순수 deterministic execution만 담당. Gemini 호출 없음.

- **Exchange SL** = 최종 안전망 (프로세스 다운/네트워크 장애 대응)
- **App TP/Regime 청산** = 전략 유연성
- **WebSocket** = 상태 동기화 (polling 대신 이벤트 기반)

---

## 파일 구조

```
src/
├── executor.py     # 신규 — REST 주문 실행 + 리스크 + run_executor_once()
└── ws_monitor.py   # 신규 — WebSocket 백그라운드 스레드

data/
├── state.json      # (기존) chart/regime 출력 — 읽기 전용
├── positions.json  # 신규 — 열린 포지션 로컬 상태
├── daily.json      # 신규 — 일일 통계 (손익/연패/거래수)
└── trades.csv      # 신규 — 거래 이력 로그
```

---

## 데이터 구조

### positions.json

```json
{
  "last_transition": "NORMAL_TO_HALT",
  "positions": [
    {
      "order_id": "123456",
      "direction": "long",
      "size_usdt": 1000.0,
      "entry_price": 50000.0,
      "sl_price": 48125.0,
      "tp_price": 53750.0,
      "sl_order_id": "789012",
      "regime": "normal",
      "leverage": 5,
      "atr_at_entry": 1250.0,
      "opened_at": "2026-05-07T12:00:00+00:00"
    }
  ]
}
```

`last_transition`: handle_regime_change() idempotent 보장용. 이미 처리한 transition이면 skip.

### daily.json

```json
{
  "date": "2026-05-07",
  "trade_count": 3,
  "wins": 2,
  "losses": 1,
  "consecutive_losses": 0,
  "daily_pnl_usdt": 350.0
}
```

날짜가 바뀌면 자동 리셋. 재시작 시 복구용.

### trades.csv 컬럼

```
opened_at, closed_at, direction, regime, leverage,
entry_price, close_price, size_usdt, sl_price, tp_price,
realized_pnl_usdt, close_reason, atr_at_entry
```

`close_reason`: "TP" | "SL_EXCHANGE" | "REGIME_CHANGE" | "EMERGENCY"

---

## executor.py 함수 구성

```
calc_trade_size(balance: float) -> float
check_risk(positions: list, daily: dict, regime: str) -> tuple[bool, str]
get_leverage(regime: str, direction: str) -> int
calc_sl_price(direction: str, entry: float, atr: float, regime: str) -> float
calc_tp_price(direction: str, entry: float, atr: float, regime: str) -> float
place_limit_order(client, direction, size_usdt, price, leverage) -> dict | None
place_stop_loss(client, direction, sl_price, size) -> dict | None
close_position_market(client, position, reason: str) -> bool
check_tp_hits(client, positions, current_price) -> None
handle_regime_change(client, positions, new_regime, prev_regime, atr, current_price: float) -> None
run_executor_once() -> None
```

---

## run_executor_once() 흐름

```
1. state.json 읽기 → regime, entry_signal, confidence, atr, regime_changed, regime_transition
2. positions.json, daily.json 읽기

3. 기존 포지션 TP 체크 (신규 진입보다 우선)
   → TP 도달 포지션 → close_position_market(reason="TP")
   → positions.json 업데이트 + daily.json + trades.csv

4. regime_changed == True → handle_regime_change()

5. 리스크 4중 체크 (하나라도 실패 → return)
   ① regime != "halt"
   ② len(positions) < 2  (방향 무관 총 합산, long+short 모두 카운트)
   ③ daily_pnl_usdt > -1500.0
   ④ consecutive_losses < 3

6. entry_signal == "none" → return

7. 잔고 조회 → calc_trade_size(balance)

8. Limit 진입 주문 제출 (3회 retry, 각 1초 대기)
   실패 → return

9. fill_events[order_id] = threading.Event()
   try:
       filled = fill_events[order_id].wait(timeout=30)
       result = fill_results.get(order_id)
   finally:
       fill_events.pop(order_id, None)
       fill_results.pop(order_id, None)

   미체결(filled=False) → 주문 취소 → return
   partial fill → 잔여 취소

10. 체결 확인 → Stop Loss 주문 제출 (3회 retry)
    모든 retry 실패 → Emergency Market Close (SL 없이 방치 금지)

11. positions.json 업데이트 (entry_price, sl_order_id, tp_price 포함)

12. 전체 try/except — 절대 raise 안 함
```

---

## ATR 기반 SL/TP (동적)

| Regime | 레버리지(Long/Short) | SL 배수 | TP 배수 |
|--------|---------------------|---------|---------|
| normal | 5x / 3x | ATR × 1.5 | ATR × 3.0 |
| caution | 3x / 2x | ATR × 1.0 | ATR × 2.0 |
| risk_off_trend | — / 2x | ATR × 1.0 | ATR × 2.0 |

```python
# Long 예시
sl_price = entry_price - atr * sl_multiplier
tp_price = entry_price + atr * tp_multiplier

# Short 예시
sl_price = entry_price + atr * sl_multiplier
tp_price = entry_price - atr * tp_multiplier
```

---

## handle_regime_change() (idempotent)

```python
transition = f"{prev_regime.upper()}_TO_{new_regime.upper()}"

# idempotent 체크
if positions_data.get("last_transition") == transition:
    return  # 이미 처리됨

# 전환 처리
if transition == "NORMAL_TO_CAUTION":
    for pos in positions:
        # 새 SL 먼저 제출 성공 확인 → 구 SL 취소 (역순 보장)
        new_sl = calc_sl_price(..., multiplier=0.8)
        new_sl_order = place_stop_loss(client, ..., new_sl)
        if new_sl_order:
            cancel_order(client, pos["sl_order_id"])
            pos["sl_price"] = new_sl
            pos["sl_order_id"] = new_sl_order["orderId"]
        # 실패 시: 구 SL 유지, 에러 로그

elif transition in ("NORMAL_TO_HALT", "CAUTION_TO_HALT", ...):
    for pos in positions:
        # current_price: state.json["BTCUSDT"]["close"] (마지막 차트 종가)
        pnl = calc_unrealized_pnl(pos, current_price)
        if pnl > 0:
            close_position_market(client, pos, reason="REGIME_CHANGE")
        else:
            # SL 타이트하게 (ATR × 0.8)
            tighten_sl(client, pos, multiplier=0.8)

# last_transition 갱신
positions_data["last_transition"] = transition
save_positions(positions_data)
```

---

## ws_monitor.py

### 구독 채널 (Private, `python-bitget` BitgetWsClient)

- `orders` 채널 → fill / partial fill / cancel 이벤트
- `positions` 채널 → position open / close / liquidation

### executor ↔ ws_monitor 공유 상태

```python
# ws_monitor 모듈 레벨 (executor에서 import)
fill_events: dict[str, threading.Event] = {}
fill_results: dict[str, dict] = {}
```

### 이벤트 핸들러

```
order fill 수신:
  → fill_results[order_id] = event_data
  → fill_events[order_id].set()  (order_id 키가 있을 때만)

position close 수신 (exchange SL 발동):
  → positions.json에서 해당 포지션 제거
  → daily.json 업데이트 (pnl, consecutive_losses)
  → trades.csv에 SL_EXCHANGE 기록

position liquidation 수신:
  → 긴급 로그 + positions.json 정리
```

### 재연결 로직

```
연결 끊김 감지
→ 5초 대기 → 재연결 시도 (최대 5회)
→ 모두 실패: 에러 로그 (프로세스 유지)
→ 재연결 성공: 채널 재구독 + REST로 open positions 상태 재확인
```

### 스레드 시작

```python
# explorer.py main()
import threading
from src.ws_monitor import run_ws_monitor

ws_thread = threading.Thread(target=run_ws_monitor, daemon=True)
ws_thread.start()
```

---

## 스케줄러 통합

`explorer.py main()` 직렬 실행 체인 확장:

```python
from src.executor import run_executor_once

def run_chart_and_regime_and_executor():
    run_chart_once()
    run_regime_once()
    run_executor_once()

scheduler.add_job(
    run_chart_and_regime_and_executor,
    "interval",
    minutes=15,
    start_date=now + timedelta(minutes=5),
    id="chart_regime_executor",
)
```

기존 `"chart_regime"` job ID → `"chart_regime_executor"` 교체.

---

## 동적 거래 금액

```python
def calc_trade_size(balance: float) -> float:
    """잔고의 10%, 최대 2000 USDT."""
    return min(2000.0, balance * 0.10)
```

| 잔고 | 거래 금액 |
|------|----------|
| 20,000 USDT | 2,000 USDT |
| 5,000 USDT | 500 USDT |
| 1,000 USDT | 100 USDT |

---

## 내결함성

- `run_executor_once()` 전체 `try/except` — 절대 raise 안 함
- SL 제출 실패 → Emergency Market Close (SL 없이 포지션 방치 금지)
- SL 재설정: 새 SL 먼저 제출 → 성공 확인 후 구 SL 취소 (역순 보장)
- WebSocket 끊김 → 재연결 (프로세스 유지)
- fill_events: `finally`에서 항상 cleanup (timeout/예외 모두 안전)

---

## 테스트 계획

### tests/test_executor.py (10개)

| 테스트 | 내용 |
|--------|------|
| `test_calc_trade_size` | 20000→2000, 5000→500, 1000→100 |
| `test_risk_check_halt_regime` | regime=halt → (False, "halt") |
| `test_risk_check_max_positions` | positions 2개 → (False, "max_positions") |
| `test_risk_check_daily_loss` | daily_pnl=-1500 → (False, "daily_loss") |
| `test_risk_check_consecutive_losses` | consecutive=3 → (False, "consecutive") |
| `test_calc_sl_tp_normal_long` | entry=50000, atr=1000 → sl=48500, tp=53000 |
| `test_place_entry_retry` | 첫 2회 실패 → 3회째 성공 |
| `test_limit_order_timeout_cancel` | 30초 fill 없음 → 주문 취소 |
| `test_sl_failure_triggers_emergency_close` | SL 3회 실패 → market close 호출 |
| `test_handle_regime_change_idempotent` | 같은 transition 두 번 → 처리 1회만 |
| `test_tp_hit_triggers_market_close` | TP 도달 포지션 → market close 호출 확인 |

### tests/test_ws_monitor.py (2개)

| 테스트 | 내용 |
|--------|------|
| `test_fill_event_set_by_order_id` | ws_monitor가 fill_events[order_id].set() 호출 확인 |
| `test_fill_event_cleanup_on_timeout` | timeout 후 fill_events, fill_results 정리 확인 |

---

## SoSoValue 버그 수정

`src/explorer.py` `collect_etf()` 수정:

```python
# 수정 전
flows = resp.json()["data"][:3]

# 수정 후 (SoSoValue API는 list 직접 반환)
flows = resp.json()[:3]
```

이 수정을 Stage 5 Task 1에 포함.
