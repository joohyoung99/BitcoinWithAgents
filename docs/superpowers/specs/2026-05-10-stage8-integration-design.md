# Stage 8: 전체 통합 및 데모 테스트 Design

## Goal

`main.py`를 전체 시스템의 단일 진입점으로 정비한다. 헬스체크(잔고 확인) 후 전체 시스템(`explorer.main()`)을 실행하며, 실패 시 `logging.error` + `sys.exit(1)`로 명확히 종료한다.

---

## Architecture

```
uv run python main.py
  │
  ├── 1. logging 기본 설정 (StreamHandler, INFO)
  │       root logger에 StreamHandler 직접 추가 (basicConfig 미사용)
  │       — FileHandler 중복 방지를 위해 핸들러 존재 여부 확인
  │
  ├── 2. 헬스체크 (timeout=10s)
  │       get_client() → get_balance() — concurrent.futures로 10초 제한
  │       성공: 잔고/가용증거금/미실현손익 logging.info 출력
  │       timeout: logging.error("헬스체크 10초 초과") → sys.exit(1)
  │       그 외 실패: logging.error(구체적 메시지) → sys.exit(1)
  │
  ├── 3. 시스템 시작 (lazy import)
  │       from src.explorer import main as run_system  ← 함수 내부 import
  │       run_system()
  │         → _setup_logging() (FileHandler "data/system.log" 추가 — 중복 방지됨)
  │         → init_db()
  │         → 스케줄러: explorer 1h / chart+regime+executor 15min
  │
  └── 4. KeyboardInterrupt
          run_system() 반환 후 또는 Ctrl+C 시
          logging.info("시스템 종료") 출력 후 정상 종료 (traceback 없음)
```

---

## src/main.py 상세

### logging 기본 설정 — FileHandler 중복 방지

`basicConfig`는 핸들러가 이미 있으면 전체를 무시한다. `_setup_logging()`도 `basicConfig`를 사용하므로, `main.py`에서 먼저 `basicConfig`를 호출하면 `_setup_logging()`의 FileHandler가 추가되지 않는다.

→ `main.py`는 root logger에 StreamHandler를 **직접** 추가하되 이미 StreamHandler가 있으면 건너뜀:

```python
root = logging.getLogger()
root.setLevel(logging.INFO)
if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
           for h in root.handlers):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)
```

`_setup_logging()`은 FileHandler를 직접 `addHandler`하므로, StreamHandler가 이미 있어도 FileHandler는 정상 추가된다.

### 헬스체크 timeout

bitpy SDK는 HTTP timeout을 직접 노출하지 않으므로 `concurrent.futures.ThreadPoolExecutor`로 래핑:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

def _healthcheck(timeout: int = 10) -> dict:
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(lambda: get_balance(get_client()))
        return future.result(timeout=timeout)
```

### 실패 처리 — except 중복 제거

```python
try:
    balance = _healthcheck(timeout=10)
except FuturesTimeout:
    logging.error("헬스체크 10초 초과 — Bitget API 응답 없음")
    sys.exit(1)
except ValueError as e:
    logging.error(f"환경변수 오류: {e}")
    sys.exit(1)
except Exception as e:
    logging.error(f"헬스체크 실패: {e}")
    sys.exit(1)
```

- `FuturesTimeout` 먼저 — timeout 전용 메시지 명시
- `ValueError` — 환경변수 누락 (`get_client()`)
- `Exception` — RuntimeError(API 오류), 네트워크 오류 등 나머지 전부

`(ValueError, RuntimeError, FuturesTimeout, Exception)` 처럼 묶으면 `Exception`이 앞의 것을 모두 삼키므로 계층형 except로 분리.

### KeyboardInterrupt graceful shutdown

```python
def main() -> None:
    _setup_stream_handler()
    balance = _run_healthcheck()  # 실패 시 내부에서 sys.exit(1)
    logging.info(f"헬스체크 통과: 잔고={balance['total']:,.2f} USDT, "
                 f"가용={balance['available']:,.2f} USDT, "
                 f"미실현PnL={balance['unrealized_pnl']:,.2f} USDT")
    try:
        from src.explorer import main as run_system
        run_system()
    except KeyboardInterrupt:
        logging.info("시스템 종료 (Ctrl+C)")
```

`explorer.main()` 내부에서도 `KeyboardInterrupt`를 잡아 반환하지만, 상위에서도 잡아 traceback 없이 종료.

### lazy import

`from src.explorer import main as run_system`을 `main()` 함수 내부, 헬스체크 통과 후에 배치. APScheduler 등 explorer 임포트 사이드이펙트를 헬스체크 실패 시 건너뜀.

---

## 로그 출력 흐름

| 시점 | 출력 내용 |
|------|-----------|
| 시작 | `[INFO] 헬스체크 통과: 잔고=X USDT, 가용=Y USDT, 미실현PnL=Z USDT` |
| timeout | `[ERROR] 헬스체크 10초 초과 — Bitget API 응답 없음` → exit |
| 환경변수 누락 | `[ERROR] 환경변수 오류: Missing env vars: ...` → exit |
| 기타 실패 | `[ERROR] 헬스체크 실패: ...` → exit |
| explorer 시작 | `[explorer] WebSocket monitor started` |
| 매 1h | `[explorer] <timestamp> risk=risk-on` |
| 매 15min | `[chart]`, `[regime]`, `[executor]` 로그 |
| 거래 발생 | `[executor] opened long order=... entry=... sl=... tp=...` |
| Ctrl+C | `[INFO] 시스템 종료 (Ctrl+C)` |

모든 로그는 `data/system.log`에도 동시 기록 (`_setup_logging()` FileHandler).

---

## Testing

**`tests/test_main.py`** (신규):

- `test_main_calls_run_system_on_success`: `_healthcheck` 성공 시 `explorer.main` 호출 확인
- `test_main_exits_on_timeout`: `FuturesTimeout` 발생 시 `sys.exit(1)` + "10초 초과" 로그 확인
- `test_main_exits_on_value_error`: `ValueError` 발생 시 `sys.exit(1)` 확인
- `test_main_exits_on_api_error`: `RuntimeError` 발생 시 `sys.exit(1)` 확인
- `test_main_handles_keyboard_interrupt`: `run_system` 중 `KeyboardInterrupt` 시 정상 종료 (예외 미전파) 확인

기존 82개 테스트 회귀 확인.

---

## 변경 파일

| 파일 | 변경 |
|------|------|
| `main.py` | 전체 교체 |
| `tests/test_main.py` | 신규 — 5개 테스트 |

---

## Error Handling

- 헬스체크 timeout/실패: `logging.error` + `sys.exit(1)` (traceback 없음)
- `KeyboardInterrupt`: `logging.info("시스템 종료")` 후 정상 종료
- 시스템 실행 중 오류: 기존 각 모듈 try/except 처리 (변경 없음)
- DB/로그 실패: 기존 `db.py` try/except 처리 (변경 없음)
