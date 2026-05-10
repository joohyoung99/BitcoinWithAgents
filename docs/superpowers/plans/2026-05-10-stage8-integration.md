# Stage 8: 전체 통합 및 데모 테스트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `main.py`를 전체 시스템의 단일 진입점으로 교체 — 헬스체크(잔고 확인, 10초 timeout) 후 전체 시스템(`src/explorer.py:main()`)을 실행한다.

**Architecture:** `main.py`는 StreamHandler 설정 → 헬스체크 → lazy import로 `explorer.main()` 호출의 3단계로 구성. 헬스체크 실패 유형별(timeout / 환경변수 / API 오류)로 `logging.error` 후 `sys.exit(1)`. `KeyboardInterrupt`는 `logging.info` 후 정상 종료.

**Tech Stack:** Python stdlib: `logging`, `concurrent.futures`, `sys`

---

## File Map

| 파일 | 변경 |
|------|------|
| `main.py` | 전체 교체 |
| `tests/test_main.py` | 신규 — 5개 테스트 |

---

## Task 1: tests/test_main.py — 5개 실패 테스트 작성

**Files:**
- Create: `tests/test_main.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_main.py` 파일을 생성한다:

```python
from __future__ import annotations

import logging
from concurrent.futures import TimeoutError as FuturesTimeout
from unittest.mock import patch

import pytest

import main as main_mod

BALANCE_OK = {"total": 20000.0, "available": 18000.0, "unrealized_pnl": 500.0}


def test_main_calls_run_system_on_success():
    with patch.object(main_mod, "_healthcheck", return_value=BALANCE_OK), \
         patch("src.explorer.main") as mock_run:
        main_mod.main()
    mock_run.assert_called_once()


def test_main_exits_on_timeout(caplog):
    with patch.object(main_mod, "_healthcheck", side_effect=FuturesTimeout()), \
         caplog.at_level(logging.ERROR), \
         pytest.raises(SystemExit) as exc_info:
        main_mod.main()
    assert exc_info.value.code == 1
    assert "10초 초과" in caplog.text


def test_main_exits_on_value_error():
    with patch.object(main_mod, "_healthcheck", side_effect=ValueError("Missing env vars: BITGET_API_KEY")), \
         pytest.raises(SystemExit) as exc_info:
        main_mod.main()
    assert exc_info.value.code == 1


def test_main_exits_on_api_error():
    with patch.object(main_mod, "_healthcheck", side_effect=RuntimeError("Bitget API error 40001")), \
         pytest.raises(SystemExit) as exc_info:
        main_mod.main()
    assert exc_info.value.code == 1


def test_main_handles_keyboard_interrupt():
    with patch.object(main_mod, "_healthcheck", return_value=BALANCE_OK), \
         patch("src.explorer.main", side_effect=KeyboardInterrupt()):
        main_mod.main()  # KeyboardInterrupt가 밖으로 전파되면 안 됨
```

- [ ] **Step 2: 테스트 실패 확인**

```
uv run pytest tests/test_main.py -v
```

Expected: 5개 모두 FAIL (현재 `main.py`에 `_healthcheck` 함수가 없음)

---

## Task 2: main.py — 전체 구현

**Files:**
- Modify: `main.py` (전체 교체)

- [ ] **Step 1: main.py 전체 교체**

```python
from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

from src.account import get_balance
from src.client import get_client


def _setup_stream_handler() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(fmt)
        root.addHandler(handler)


def _healthcheck(timeout: int = 10) -> dict:
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(lambda: get_balance(get_client()))
        return future.result(timeout=timeout)


def main() -> None:
    _setup_stream_handler()

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

    logging.info(
        f"헬스체크 통과: 잔고={balance['total']:,.2f} USDT, "
        f"가용={balance['available']:,.2f} USDT, "
        f"미실현PnL={balance['unrealized_pnl']:,.2f} USDT"
    )

    try:
        from src.explorer import main as run_system  # lazy: 헬스체크 통과 후 임포트
        run_system()
    except KeyboardInterrupt:
        logging.info("시스템 종료 (Ctrl+C)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 테스트 통과 확인**

```
uv run pytest tests/test_main.py -v
```

Expected: 5 passed

---

## Task 3: 전체 회귀 확인 + 커밋

**Files:**
- 변경 없음 (확인만)

- [ ] **Step 1: 전체 테스트 회귀 확인**

```
uv run pytest tests/ -v
```

Expected: 87 passed (기존 82 + 신규 5), 0 failed

- [ ] **Step 2: 커밋**

```bash
git add main.py tests/test_main.py
git commit -m "feat: main.py — healthcheck + lazy explorer.main() entry point"
```

- [ ] **Step 3: README Stage 8 체크박스 업데이트**

`README.md`에서:
```
- [ ] 8단계: 전체 통합 및 데모 테스트
```
를:
```
- [x] 8단계: 전체 통합 및 데모 테스트
```
로 변경 후:

```bash
git add README.md
git commit -m "docs: mark Stage 8 integration complete in README"
```
