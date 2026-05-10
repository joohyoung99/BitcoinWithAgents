# Stage 8: 전체 통합 및 데모 테스트 Design

## Goal

`main.py`를 전체 시스템의 단일 진입점으로 정비한다. 헬스체크(잔고 확인) 후 전체 시스템(`explorer.main()`)을 실행하며, 실패 시 `logging.error` + `sys.exit(1)`로 명확히 종료한다.

---

## Architecture

```
uv run python main.py
  │
  ├── 1. logging 기본 설정 (StreamHandler, INFO)
  │       explorer.main()이 FileHandler를 추가하므로 여기서는 StreamHandler만
  │
  ├── 2. 헬스체크 (timeout=10s)
  │       get_client() → get_balance() — concurrent.futures로 10초 제한
  │       성공: 잔고/가용증거금/미실현손익 logging.info 출력
  │       실패: logging.error(메시지) → sys.exit(1)
  │
  └── 3. 시스템 시작 (lazy import)
          from src.explorer import main as run_system  ← 함수 내부 import
          run_system()
            → _setup_logging() (FileHandler "data/system.log" 추가)
            → init_db()
            → 스케줄러: explorer 1h / chart+regime+executor 15min
```

---

## src/main.py 상세

### logging 기본 설정

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
```

`explorer.main()`의 `_setup_logging()`이 `FileHandler("data/system.log")`를 추가한다.
`basicConfig`는 핸들러가 이미 있으면 무시되므로, `run_system()` 호출 전에 설정해야 StreamHandler가 먼저 등록된다.

### 헬스체크 timeout

bitpy SDK는 HTTP timeout을 직접 노출하지 않으므로 `concurrent.futures.ThreadPoolExecutor`로 래핑:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

def _healthcheck(timeout: int = 10) -> dict:
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(lambda: get_balance(get_client()))
        return future.result(timeout=timeout)
```

### 실패 처리

```python
except (ValueError, RuntimeError, FuturesTimeout, Exception) as e:
    logging.error(f"헬스체크 실패: {e}")
    sys.exit(1)
```

- `ValueError`: 환경변수 누락 (`get_client()` 발생)
- `RuntimeError`: API 오류 (`get_balance()` 발생)
- `FuturesTimeout`: 10초 초과
- 나머지 Exception: 네트워크 오류 등

### lazy import

```python
def main() -> None:
    # ... 헬스체크 ...
    from src.explorer import main as run_system  # lazy: 헬스체크 통과 후 임포트
    run_system()
```

APScheduler 등 explorer의 임포트 사이드이펙트를 헬스체크 실패 시 건너뜀.

---

## 로그 출력 흐름

시스템 실행 중 콘솔에 찍히는 정보:

| 시점 | 출력 내용 |
|------|-----------|
| 시작 | `[INFO] 헬스체크 통과: 잔고=X USDT, 가용=Y USDT, 미실현PnL=Z USDT` |
| explorer 실행 | `[explorer] WebSocket monitor started` |
| 매 1h | `[explorer] <timestamp> risk=risk-on` |
| 매 15min | `[chart]`, `[regime]`, `[executor]` 로그 |
| 거래 발생 | `[executor] opened long order=... entry=... sl=... tp=...` |
| 오류 | `[ERROR] 헬스체크 실패: ...` → 프로세스 종료 |

모든 로그는 `data/system.log`에도 동시 기록된다 (`_setup_logging()` FileHandler).

---

## Testing

**`tests/test_main.py`** (신규):

- `test_main_calls_run_system_on_success`: `get_balance` 성공 시 `explorer.main` 호출 확인
- `test_main_exits_on_healthcheck_failure`: `get_balance` 실패(`RuntimeError`) 시 `sys.exit(1)` 확인

기존 82개 테스트 회귀 확인.

---

## 변경 파일

| 파일 | 변경 |
|------|------|
| `main.py` | 전체 교체 |
| `tests/test_main.py` | 신규 — 2개 테스트 |

---

## Error Handling

- 헬스체크 실패는 항상 `logging.error` + `sys.exit(1)`로 즉시 종료
- 시스템 실행 중 오류는 기존 각 모듈의 try/except가 처리 (변경 없음)
- DB/로그 실패는 기존 `db.py` try/except가 처리 (변경 없음)
