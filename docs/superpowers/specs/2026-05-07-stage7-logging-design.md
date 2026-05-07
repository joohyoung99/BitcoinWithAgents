# Stage 7: 로그 시스템 Design

## Goal

모든 API 호출, LLM 응답, 주요 시스템 이벤트를 `data/trading.db` (SQLite)에 저장하고, Python logging을 통해 `data/system.log` 파일에도 기록한다. 기존 `print()` 호출은 건드리지 않는다.

## Architecture

중앙 `src/db.py` 모듈이 SQLite 연결과 모든 로그 함수를 담당한다. 각 모듈은 db.py 함수를 명시적으로 호출한다. DB 로깅 실패는 절대 메인 로직을 중단시키지 않는다 (전체 try/except 래핑).

Python logging 파일 핸들러는 `main()`에서 한 번만 설정한다. 기존 `print()`는 유지하고, `src/watchdog.py`의 `logging.warning()` 호출이 자동으로 파일에도 기록된다.

## Tech Stack

Python stdlib: `sqlite3`, `logging`, `time`

---

## src/db.py (신규)

### DB 경로

```python
DB_PATH = Path("data/trading.db")
```

### 테이블 스키마

```sql
CREATE TABLE IF NOT EXISTS api_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    request_json  TEXT,
    response_json TEXT,
    status_code TEXT,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS llm_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT NOT NULL,
    module       TEXT NOT NULL,
    model        TEXT NOT NULL,
    prompt_preview TEXT,
    response_text  TEXT,
    duration_ms  INTEGER,
    success      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL,
    level      TEXT NOT NULL,
    module     TEXT NOT NULL,
    message    TEXT NOT NULL,
    extra_json TEXT
);
```

### 공개 함수

```python
def init_db() -> None:
    """DB_PATH 부모 디렉터리 생성 + 3개 테이블 초기화. 멱등."""

def log_api(
    endpoint: str,
    request_body: dict,
    response_body: dict | None,
    duration_ms: int,
) -> None:
    """api_logs에 1행 삽입. status_code = response_body.get('code') or 'ERR'."""

def log_llm(
    module: str,
    model: str,
    prompt: str,
    response_text: str,
    duration_ms: int,
    success: bool = True,
) -> None:
    """llm_logs에 1행 삽입. prompt_preview = prompt[:200]."""

def log_event(
    level: str,
    module: str,
    message: str,
    extra: dict | None = None,
) -> None:
    """events에 1행 삽입. level = 'INFO' | 'WARNING' | 'ERROR'."""
```

모든 함수는 `try/except Exception` 으로 래핑 — 로깅 실패 시 `print()` 경고 출력 후 조용히 반환.

### DB 연결 전략

함수 호출마다 `sqlite3.connect(DB_PATH)` — 단순하고 스레드 안전 (각 스레드가 자체 연결 사용). WAL 모드 활성화:

```python
conn.execute("PRAGMA journal_mode=WAL")
```

---

## src/gemini.py (수정)

`_Model.generate_content()` 를 타이밍 + 로깅으로 감싼다:

```python
def generate_content(self, prompt: str) -> Any:
    t0 = time.time()
    try:
        result = _get_client().models.generate_content(
            model=self._model_name, contents=prompt
        )
        duration_ms = int((time.time() - t0) * 1000)
        log_llm(
            module=_caller_module(),   # inspect.stack()으로 호출 모듈명 추출
            model=self._model_name,
            prompt=prompt,
            response_text=result.text,
            duration_ms=duration_ms,
            success=True,
        )
        return result
    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        log_llm(
            module=_caller_module(),
            model=self._model_name,
            prompt=prompt,
            response_text=str(e),
            duration_ms=duration_ms,
            success=False,
        )
        raise
```

`_caller_module()`: `inspect.stack()`으로 gemini.py가 아닌 첫 번째 프레임의 모듈명 반환.

---

## src/executor.py (수정)

`_bitget_post()` 에 타이밍 + 로깅 추가. HTTP 예외로 result가 없을 경우 response_body=None으로 기록:

```python
def _bitget_post(client, endpoint: str, body: dict) -> dict | None:
    t0 = time.time()
    result = None
    try:
        # ... 기존 HTTP 로직 ...
        result = resp.json()
    except Exception as e:
        print(f"[executor] POST {endpoint} failed: {e}")
    finally:
        duration_ms = int((time.time() - t0) * 1000)
        log_api(endpoint, body, result, duration_ms)
    return result
```

---

## src/regime.py (수정)

`update_regime_state()` 에서 regime 변경 시 이벤트 기록:

```python
if rs.regime_changed:
    log_event(
        "INFO", "regime",
        f"{rs.prev_regime} → {rs.regime}",
        extra={"transition": rs.regime_transition, "symbol": rs.symbol},
    )
```

---

## src/explorer.py (수정)

### Python logging 설정 — main() 시작 시

```python
def _setup_logging() -> None:
    Path("data").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("data/system.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

def main() -> None:
    _setup_logging()
    init_db()
    # ... 기존 로직 ...
```

### risk 변경 이벤트 기록 — save_report() 직후

```python
log_event(
    "INFO", "explorer",
    f"risk={report.risk}",
    extra={"summary": report.summary, "timestamp": report.timestamp},
)
```

---

## 기존 trades.csv 유지

`trades.csv`는 그대로 유지한다. SQLite `trades` 테이블은 만들지 않는다 (중복 작업 방지, YAGNI).

---

## Error Handling

- `db.py`의 모든 log 함수: try/except로 래핑, 실패 시 `print(f"[db] log failed: {e}")` 후 반환
- `generate_content()` 실패 시: log_llm(success=False)로 기록 후 예외 재발생 (기존 호출자의 except가 처리)
- `_bitget_post()` 실패 시: 기존 None 반환 유지, 가능한 범위에서 log_api 호출

---

## Testing

**`tests/test_db.py`** (신규):
- `test_init_db_creates_tables`: `init_db()` 후 3개 테이블 존재 확인
- `test_log_api_inserts_row`: `log_api()` 호출 후 `api_logs`에 1행 삽입 확인
- `test_log_llm_inserts_row`: `log_llm()` 호출 후 `llm_logs`에 1행 삽입 확인
- `test_log_event_inserts_row`: `log_event()` 호출 후 `events`에 1행 삽입 확인
- `test_log_api_does_not_raise_on_bad_db`: DB 경로 강제 오류 시 예외 없이 반환 확인

모든 테스트는 `tmp_path` fixture로 임시 DB 사용 (실제 `data/trading.db` 오염 없음).
