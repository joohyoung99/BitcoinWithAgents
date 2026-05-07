# Stage 7: 로그 시스템 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모든 API 호출, LLM 응답, 주요 시스템 이벤트를 SQLite DB(`data/trading.db`)와 파일 로그(`data/system.log`)에 저장한다.

**Architecture:** 중앙 `src/db.py` 모듈이 SQLite 연결과 3개 로그 함수를 담당한다. `src/gemini.py`는 `generate_content()`에 타이밍 계측과 `log_llm()`을 추가하고, `src/executor.py`는 `_bitget_post()`에 `finally` 블록으로 `log_api()`를 보장한다. `src/regime.py`는 regime 변경 시, `src/explorer.py`는 risk 보고서 저장 시 `log_event()`를 호출한다. Python `logging` 설정은 `main()`에서 한 번만 수행한다.

**Tech Stack:** Python stdlib: `sqlite3`, `logging`, `time`, `inspect`

---

## File Map

| 파일 | 변경 |
|------|------|
| `src/db.py` | 신규 — SQLite init_db(), log_api(), log_llm(), log_event() |
| `tests/test_db.py` | 신규 — 5개 테스트 |
| `src/gemini.py` | 수정 — generate_content() 타이밍 + log_llm() + _caller_module() |
| `tests/test_gemini.py` | 신규 — 2개 테스트 |
| `src/executor.py` | 수정 — _bitget_post() finally 블록 + log_api() |
| `tests/test_executor_log.py` | 신규 — 2개 테스트 |
| `src/regime.py` | 수정 — regime 변경 시 log_event() |
| `tests/test_regime_log.py` | 신규 — 1개 테스트 |
| `src/explorer.py` | 수정 — _setup_logging() + init_db() + log_event() after save_report() |
| `tests/test_explorer_log.py` | 신규 — 1개 테스트 |

---

## Task 1: src/db.py — SQLite 로그 모듈

**Files:**
- Create: `src/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_db.py` 파일을 생성한다:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test_trading.db")


def test_init_db_creates_tables():
    db_mod.init_db()
    conn = sqlite3.connect(db_mod.DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert {"api_logs", "llm_logs", "events"} <= tables


def test_log_api_inserts_row():
    db_mod.init_db()
    db_mod.log_api("/test/endpoint", {"key": "val"}, {"code": "00000"}, 123)
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT endpoint, status_code, duration_ms FROM api_logs").fetchone()
    conn.close()
    assert row == ("/test/endpoint", "00000", 123)


def test_log_llm_inserts_row():
    db_mod.init_db()
    db_mod.log_llm("test_module", "gemini-2.5-flash", "hello prompt", "hello response", 456, success=True)
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT module, model, success FROM llm_logs").fetchone()
    conn.close()
    assert row == ("test_module", "gemini-2.5-flash", 1)


def test_log_event_inserts_row():
    db_mod.init_db()
    db_mod.log_event("INFO", "regime", "normal → caution", extra={"symbol": "BTCUSDT"})
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT level, module, message FROM events").fetchone()
    conn.close()
    assert row == ("INFO", "regime", "normal → caution")


def test_log_api_does_not_raise_on_bad_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", Path("/nonexistent_dir/bad.db"))
    db_mod.log_api("/test", {}, None, 0)  # must not raise
```

- [ ] **Step 2: 테스트 실패 확인**

```
uv run pytest tests/test_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.db'`

- [ ] **Step 3: src/db.py 구현**

```python
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("data/trading.db")


def init_db() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                endpoint      TEXT NOT NULL,
                request_json  TEXT,
                response_json TEXT,
                status_code   TEXT,
                duration_ms   INTEGER
            );
            CREATE TABLE IF NOT EXISTS llm_logs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp      TEXT NOT NULL,
                module         TEXT NOT NULL,
                model          TEXT NOT NULL,
                prompt_preview TEXT,
                response_text  TEXT,
                duration_ms    INTEGER,
                success        INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT NOT NULL,
                level      TEXT NOT NULL,
                module     TEXT NOT NULL,
                message    TEXT NOT NULL,
                extra_json TEXT
            );
        """)


def log_api(
    endpoint: str,
    request_body: dict,
    response_body: dict | None,
    duration_ms: int,
) -> None:
    try:
        status_code = (response_body or {}).get("code", "ERR") if response_body else "ERR"
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO api_logs "
                "(timestamp, endpoint, request_json, response_json, status_code, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    endpoint,
                    json.dumps(request_body, ensure_ascii=False),
                    json.dumps(response_body, ensure_ascii=False) if response_body is not None else None,
                    status_code,
                    duration_ms,
                ),
            )
    except Exception as e:
        print(f"[db] log_api failed: {e}")


def log_llm(
    module: str,
    model: str,
    prompt: str,
    response_text: str,
    duration_ms: int,
    success: bool = True,
) -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO llm_logs "
                "(timestamp, module, model, prompt_preview, response_text, duration_ms, success) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    module,
                    model,
                    prompt[:200],
                    response_text,
                    duration_ms,
                    1 if success else 0,
                ),
            )
    except Exception as e:
        print(f"[db] log_llm failed: {e}")


def log_event(
    level: str,
    module: str,
    message: str,
    extra: dict | None = None,
) -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO events (timestamp, level, module, message, extra_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    level,
                    module,
                    message,
                    json.dumps(extra, ensure_ascii=False) if extra is not None else None,
                ),
            )
    except Exception as e:
        print(f"[db] log_event failed: {e}")
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_db.py -v
```

Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add src/db.py tests/test_db.py
git commit -m "feat: db.py — SQLite log module (api_logs, llm_logs, events)"
```

---

## Task 2: src/gemini.py — LLM 호출 타이밍 + log_llm()

**Files:**
- Modify: `src/gemini.py`
- Test: `tests/test_gemini.py` (신규)

`generate_content()`에 타이밍 계측 + `log_llm()` 추가. 성공/실패 모두 기록하고 실패 시 예외 재발생.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_gemini.py` 파일을 생성한다:

```python
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db()


def test_generate_content_logs_llm_on_success():
    mock_result = MagicMock()
    mock_result.text = "test response"

    with patch("src.gemini._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_result
        mock_get_client.return_value = mock_client

        from src.gemini import get_model
        result = get_model("gemini-2.5-flash").generate_content("test prompt")

    assert result.text == "test response"

    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT module, model, success FROM llm_logs").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "gemini-2.5-flash"
    assert row[2] == 1


def test_generate_content_logs_llm_on_failure():
    with patch("src.gemini._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API error")
        mock_get_client.return_value = mock_client

        from src.gemini import get_model
        with pytest.raises(RuntimeError):
            get_model("gemini-2.5-flash").generate_content("fail prompt")

    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT success FROM llm_logs").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0
```

- [ ] **Step 2: 테스트 실패 확인**

```
uv run pytest tests/test_gemini.py -v
```

Expected: FAIL — no rows in llm_logs (log_llm not called yet)

- [ ] **Step 3: src/gemini.py 전체 교체**

```python
from __future__ import annotations

import inspect
import os
import time
from typing import Any

import dotenv
from google import genai

from src.db import log_llm

dotenv.load_dotenv()

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        project = os.getenv("VERTEX_PROJECT_ID", "")
        location = os.getenv("VERTEX_LOCATION", "us-central1")
        _client = genai.Client(vertexai=True, project=project, location=location)
    return _client


def _caller_module() -> str:
    for frame_info in inspect.stack():
        mod = frame_info[0].f_globals.get("__name__", "")
        if mod and mod != __name__ and not mod.startswith("_"):
            return mod.split(".")[-1]
    return "unknown"


class _Model:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    def generate_content(self, prompt: str) -> Any:
        t0 = time.time()
        try:
            result = _get_client().models.generate_content(
                model=self._model_name,
                contents=prompt,
            )
            duration_ms = int((time.time() - t0) * 1000)
            log_llm(
                module=_caller_module(),
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


def get_model(model_name: str) -> _Model:
    return _Model(model_name)
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_gemini.py -v
```

Expected: 2 passed

- [ ] **Step 5: 전체 테스트 회귀 확인**

```
uv run pytest tests/ -v
```

Expected: 전체 통과

- [ ] **Step 6: 커밋**

```bash
git add src/gemini.py tests/test_gemini.py
git commit -m "feat: gemini.py — log_llm on every generate_content call"
```

---

## Task 3: src/executor.py — _bitget_post() 타이밍 + log_api()

**Files:**
- Modify: `src/executor.py` (`_bitget_post` 함수, 130~140행)
- Test: `tests/test_executor_log.py` (신규)

`finally` 블록으로 HTTP 성공/실패 모두 `log_api()` 호출을 보장한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_executor_log.py` 파일을 생성한다:

```python
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db()


def _make_client(json_return=None, side_effect=None):
    mock_client = MagicMock()
    rh = mock_client.account.request_handler
    rh.base_url = "https://api.bitget.com"
    rh._get_headers.return_value = {}
    if side_effect:
        rh.session.post.side_effect = side_effect
    else:
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_return
        rh.session.post.return_value = mock_resp
    return mock_client


def test_bitget_post_logs_api_on_success():
    client = _make_client(json_return={"code": "00000", "data": {}})
    from src.executor import _bitget_post
    result = _bitget_post(client, "/api/v2/mix/order/place-order", {"symbol": "BTCUSDT"})

    assert result == {"code": "00000", "data": {}}
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT endpoint, status_code FROM api_logs").fetchone()
    conn.close()
    assert row == ("/api/v2/mix/order/place-order", "00000")


def test_bitget_post_logs_api_on_failure():
    client = _make_client(side_effect=ConnectionError("timeout"))
    from src.executor import _bitget_post
    result = _bitget_post(client, "/api/v2/mix/order/place-order", {"symbol": "BTCUSDT"})

    assert result is None
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT endpoint, status_code FROM api_logs").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "ERR"
```

- [ ] **Step 2: 테스트 실패 확인**

```
uv run pytest tests/test_executor_log.py -v
```

Expected: FAIL — no rows in api_logs

- [ ] **Step 3: src/executor.py 수정 — _bitget_post() 함수 교체**

`src/executor.py`의 `_bitget_post()` 함수 전체(130~140행)를 다음으로 교체한다:

```python
def _bitget_post(client, endpoint: str, body: dict) -> dict | None:
    import json as _json
    import time as _time
    from src.db import log_api
    rh = client.account.request_handler
    body_str = _json.dumps(body)
    t0 = _time.time()
    result = None
    try:
        headers = rh._get_headers("POST", endpoint, "", body_str)
        resp = rh.session.post(f"{rh.base_url}{endpoint}", headers=headers, data=body_str)
        result = resp.json()
        return result
    except Exception as e:
        print(f"[executor] POST {endpoint} failed: {e}")
        return None
    finally:
        duration_ms = int((_time.time() - t0) * 1000)
        log_api(endpoint, body, result, duration_ms)
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_executor_log.py -v
```

Expected: 2 passed

- [ ] **Step 5: 커밋**

```bash
git add src/executor.py tests/test_executor_log.py
git commit -m "feat: executor.py — log_api on every _bitget_post call"
```

---

## Task 4: src/regime.py — regime 변경 시 log_event()

**Files:**
- Modify: `src/regime.py`
- Test: `tests/test_regime_log.py` (신규)

`run_regime_once()`에서 `update_regime_state()` 직후, `regime_changed`가 True일 때 `log_event()`를 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_log.py` 파일을 생성한다:

```python
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db()


def test_regime_change_logs_event(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "BTCUSDT": {
            "trend_range": "trend",
            "risk": "risk-on",
            "signal_summary": "테스트",
            "regime": "caution",         # prev_regime — differs from new
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    }), encoding="utf-8")

    import src.regime as regime_mod
    monkeypatch.setattr(regime_mod, "STATE_PATH", state_path)

    mock_response = MagicMock()
    mock_response.text = '{"regime": "normal", "comment": "추세 강화"}'

    with patch("src.regime.get_model") as mock_get_model:
        mock_get_model.return_value.generate_content.return_value = mock_response
        regime_mod.run_regime_once()

    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT level, module, message FROM events").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "INFO"
    assert row[1] == "regime"
    assert "CAUTION_TO_NORMAL" in row[2]
```

- [ ] **Step 2: 테스트 실패 확인**

```
uv run pytest tests/test_regime_log.py -v
```

Expected: FAIL — no rows in events

- [ ] **Step 3: src/regime.py 수정 — 임포트 추가**

`src/regime.py` 상단의 `from src.gemini import get_model` 다음 줄에 추가한다:

```python
from src.db import log_event
```

- [ ] **Step 4: src/regime.py 수정 — run_regime_once()에 log_event() 추가**

`run_regime_once()` 함수의 `update_regime_state(rs)` 직후 (change_str 계산 직전) 다음 블록을 삽입한다:

현재:
```python
        update_regime_state(rs)

        change_str = f" [{regime_transition}]" if regime_changed else ""
        print(f"[regime] {rs.regime_updated_at} regime={regime}{change_str}")
```

다음으로 교체:
```python
        update_regime_state(rs)

        if regime_changed:
            log_event(
                "INFO", "regime",
                rs.regime_transition,
                extra={"prev_regime": rs.prev_regime, "regime": rs.regime, "symbol": rs.symbol},
            )

        change_str = f" [{regime_transition}]" if regime_changed else ""
        print(f"[regime] {rs.regime_updated_at} regime={regime}{change_str}")
```

- [ ] **Step 5: 테스트 통과 확인**

```
uv run pytest tests/test_regime_log.py -v
```

Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/regime.py tests/test_regime_log.py
git commit -m "feat: regime.py — log_event on regime change"
```

---

## Task 5: src/explorer.py — _setup_logging() + init_db() + log_event()

**Files:**
- Modify: `src/explorer.py`
- Test: `tests/test_explorer_log.py` (신규)

`main()`에 Python logging 설정과 `init_db()` 호출 추가. `run_once()`의 `save_report()` 직후 `log_event()` 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_explorer_log.py` 파일을 생성한다:

```python
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import src.db as db_mod
from src.models import ExplorerReport, FearGreedData, MarketData


def test_run_once_logs_risk_event(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db()

    import src.explorer as explorer_mod
    monkeypatch.setattr(explorer_mod, "REPORT_PATH", tmp_path / "report.json")

    fg = FearGreedData(score=70, label="Greed")
    market = MarketData(funding_rate=0.005, open_interest=1e9, long_short_ratio=1.2)
    report = ExplorerReport(
        timestamp="2026-05-07T00:00:00+00:00",
        risk="risk-on",
        summary="테스트",
        macro=None,
        etf=None,
        fear_greed=fg,
        market=market,
    )

    with patch("src.explorer.collect_macro", return_value=None), \
         patch("src.explorer.collect_etf", return_value=None), \
         patch("src.explorer.collect_fear_greed", return_value=fg), \
         patch("src.explorer.collect_market", return_value=market), \
         patch("src.explorer.analyse", return_value=report):
        explorer_mod.run_once()

    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT level, module, message FROM events").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "INFO"
    assert row[1] == "explorer"
    assert "risk-on" in row[2]
```

- [ ] **Step 2: 테스트 실패 확인**

```
uv run pytest tests/test_explorer_log.py -v
```

Expected: FAIL — no rows in events

- [ ] **Step 3: src/explorer.py 수정 — 임포트 추가**

`src/explorer.py`에서 `from src.regime import run_regime_once` 다음 줄에 추가한다:

```python
from src.db import init_db, log_event
```

- [ ] **Step 4: src/explorer.py 수정 — _setup_logging() 함수 추가**

`REPORT_PATH = Path("data/explorer_report.json")` 줄 직후에 새 함수를 추가한다:

```python
def _setup_logging() -> None:
    import logging
    Path("data").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("data/system.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
```

- [ ] **Step 5: src/explorer.py 수정 — run_once()에 log_event() 추가**

`run_once()` 함수의 `save_report(report)` 줄을 찾아 다음 블록으로 교체한다:

현재:
```python
        save_report(report)
        print(f"[explorer] {report.timestamp} risk={report.risk}")
```

다음으로 교체:
```python
        save_report(report)
        log_event(
            "INFO", "explorer",
            f"risk={report.risk}",
            extra={"summary": report.summary, "timestamp": report.timestamp},
        )
        print(f"[explorer] {report.timestamp} risk={report.risk}")
```

- [ ] **Step 6: src/explorer.py 수정 — main()에 _setup_logging() + init_db() 추가**

`main()` 함수의 첫 줄 직후, `def _job_explorer()` 정의 전에 추가한다:

현재:
```python
def main() -> None:
    def _job_explorer() -> None:
```

다음으로 교체:
```python
def main() -> None:
    _setup_logging()
    init_db()

    def _job_explorer() -> None:
```

- [ ] **Step 7: 테스트 통과 확인**

```
uv run pytest tests/test_explorer_log.py -v
```

Expected: PASS

- [ ] **Step 8: 전체 테스트 통과 확인**

```
uv run pytest tests/ -v
```

Expected: 전체 통과 (기존 + 신규 11개 이상)

- [ ] **Step 9: 커밋**

```bash
git add src/explorer.py tests/test_explorer_log.py
git commit -m "feat: explorer.py — setup_logging + init_db + log_event on risk report"
```
