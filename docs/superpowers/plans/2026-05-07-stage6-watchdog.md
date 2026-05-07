# Stage 6: Watchdog + 상태 복구 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 메인 스케줄러가 20분 이상 heartbeat 없으면 자동 재시작하고, 3회 초과 시 sys.exit(1)하는 Watchdog 스레드를 구현한다.

**Architecture:** `src/watchdog.py`에 모듈 수준 `beat()` / `start(scheduler, stale_minutes, check_interval)` 함수를 두고, `src/explorer.py` `main()`을 while 재시작 루프로 리팩터링한다. 각 스케줄러 잡은 실행 시작 시 `beat()`를 호출해 heartbeat를 갱신한다. 상태 복구는 기존 JSON 파일 읽기 패턴이 이미 처리하므로 추가 코드 없음.

**Tech Stack:** Python threading, APScheduler BlockingScheduler

---

## File Map

| 파일 | 변경 |
|------|------|
| `src/watchdog.py` | 신규 생성 |
| `src/explorer.py` | `main()` 리팩터 + `BlockingScheduler` 임포트를 모듈 상단으로 이동 |
| `tests/test_watchdog.py` | 신규 생성 |
| `tests/test_explorer.py` | `test_main_exits_after_max_restarts` 추가 |

---

## Task 1: src/watchdog.py — beat() + start()

**Files:**
- Create: `src/watchdog.py`
- Test: `tests/test_watchdog.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_watchdog.py` 파일을 생성한다:

```python
import threading
import time
from unittest.mock import MagicMock

import pytest


def test_beat_resets_state():
    from src import watchdog
    watchdog._last_beat = 0.0
    watchdog.beat()
    assert watchdog._last_beat > 0.0


def test_start_detects_stale_and_calls_shutdown():
    from src import watchdog

    watchdog._last_beat = time.time() - 9999  # 오래된 heartbeat
    mock_scheduler = MagicMock()

    watchdog.start(mock_scheduler, stale_minutes=0, check_interval=0.05)
    time.sleep(0.3)  # watchdog 스레드가 체크할 시간 확보

    mock_scheduler.shutdown.assert_called_once_with(wait=False)


def test_start_does_not_shutdown_when_fresh():
    from src import watchdog

    watchdog.beat()  # 방금 갱신
    mock_scheduler = MagicMock()

    watchdog.start(mock_scheduler, stale_minutes=999, check_interval=0.05)
    time.sleep(0.3)

    mock_scheduler.shutdown.assert_not_called()
```

- [ ] **Step 2: 테스트 실패 확인**

```
uv run pytest tests/test_watchdog.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.watchdog'`

- [ ] **Step 3: src/watchdog.py 구현**

```python
from __future__ import annotations

import threading
import time

_last_beat: float = time.time()
_lock = threading.Lock()


def beat() -> None:
    global _last_beat
    with _lock:
        _last_beat = time.time()


def start(scheduler, stale_minutes: int = 20, check_interval: float = 300.0) -> None:
    stale_seconds = stale_minutes * 60

    def _loop() -> None:
        while True:
            time.sleep(check_interval)
            with _lock:
                age = time.time() - _last_beat
            if age > stale_seconds:
                print(f"[watchdog] heartbeat stale ({age:.0f}s) — shutting down scheduler")
                try:
                    scheduler.shutdown(wait=False)
                except Exception as e:
                    print(f"[watchdog] shutdown failed: {e}")
                return

    t = threading.Thread(target=_loop, daemon=True, name="watchdog")
    t.start()
```

- [ ] **Step 4: 테스트 통과 확인**

```
uv run pytest tests/test_watchdog.py -v
```

Expected: 3 passed

- [ ] **Step 5: 커밋**

```
git add src/watchdog.py tests/test_watchdog.py
git commit -m "feat: watchdog beat() + start() with stale detection"
```

---

## Task 2: explorer.py main() — 재시작 루프 리팩터링

**Files:**
- Modify: `src/explorer.py`
- Test: `tests/test_explorer.py` (테스트 추가)

현재 `main()`은 `BlockingScheduler`를 한 번만 시작하고 종료된다. 이를 최대 3회 재시작을 지원하는 while 루프로 리팩터링한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_explorer.py` 파일 맨 아래에 추가한다:

```python
def test_main_exits_after_max_restarts(monkeypatch):
    import sys
    import src.explorer as explorer_mod
    from src.explorer import main

    # scheduler.start()가 즉시 반환 → 매번 재시작 트리거
    mock_scheduler = MagicMock()
    mock_scheduler.start.return_value = None

    with patch("src.explorer.BlockingScheduler", return_value=mock_scheduler), \
         patch("src.explorer.run_once"), \
         patch("src.explorer.run_chart_once"), \
         patch("src.explorer.run_regime_once"), \
         patch("src.explorer.run_executor_once"), \
         patch("src.explorer.run_ws_monitor"), \
         patch("src.explorer.time.sleep"), \
         patch("src.explorer.watchdog.start"), \
         patch("src.explorer.watchdog.beat"), \
         pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
```

`tests/test_explorer.py` 파일 상단 임포트에 `import pytest`가 없으면 추가한다 (이미 있음).

- [ ] **Step 2: 테스트 실패 확인**

```
uv run pytest tests/test_explorer.py::test_main_exits_after_max_restarts -v
```

Expected: FAIL — `src.explorer` 에 `BlockingScheduler`가 모듈 수준 임포트로 없거나 `watchdog` 임포트가 없어서 패치 불가

- [ ] **Step 3: src/explorer.py 리팩터링**

`src/explorer.py` 상단 임포트 블록을 수정한다. 현재:

```python
import requests

from src.gemini import get_model
from src.models import (
    ETFData,
    ExplorerReport,
    FearGreedData,
    MacroData,
    MarketData,
)
```

다음으로 교체한다 (모듈 수준 임포트 추가):

```python
import sys
import time
import threading

import requests
from apscheduler.schedulers.blocking import BlockingScheduler

from src import watchdog
from src.chart import run_chart_once
from src.executor import run_executor_once
from src.gemini import get_model
from src.models import (
    ETFData,
    ExplorerReport,
    FearGreedData,
    MacroData,
    MarketData,
)
from src.regime import run_regime_once
from src.ws_monitor import run_ws_monitor
```

- [ ] **Step 4: main() 함수 전체 교체**

현재 `main()` 함수(라인 311~349)를 다음으로 교체한다:

```python
def main() -> None:
    def _job_explorer() -> None:
        watchdog.beat()
        run_once()

    def _job_chain() -> None:
        watchdog.beat()
        run_chart_once()
        run_regime_once()
        run_executor_once()

    ws_thread = threading.Thread(target=run_ws_monitor, daemon=True, name="ws_monitor")
    ws_thread.start()
    print("[explorer] WebSocket monitor started")

    print("[explorer] starting — running once immediately")
    _job_explorer()
    _job_chain()

    MAX_RESTARTS = 3
    restart_count = 0

    while True:
        watchdog.beat()
        now = datetime.now(UTC)
        scheduler = BlockingScheduler()
        scheduler.add_job(_job_explorer, "interval", hours=1, id="explorer")
        scheduler.add_job(
            _job_chain,
            "interval",
            minutes=15,
            start_date=now + timedelta(minutes=5),
            id="chart_regime_executor",
            misfire_grace_time=60,
        )
        watchdog.start(scheduler, stale_minutes=20)
        print("[explorer] scheduler started — explorer 1h, chart+regime+executor 15min (Ctrl+C to stop)")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("[explorer] shutdown requested")
            return

        restart_count += 1
        if restart_count >= MAX_RESTARTS:
            print(f"[watchdog] {MAX_RESTARTS} restarts exhausted — exiting")
            sys.exit(1)

        print(f"[explorer] restarting (attempt {restart_count})")
        time.sleep(5)
```

- [ ] **Step 5: 기존 local 임포트 제거 확인**

`main()` 안에 남아 있는 `import threading`, `from apscheduler...`, `from src.chart...` 등 로컬 임포트가 없는지 확인한다. Step 4의 새 코드에는 없으므로 교체 후 자동으로 제거됨.

- [ ] **Step 6: 전체 테스트 통과 확인**

```
uv run pytest tests/ -v
```

Expected: 70 passed (기존 67 + 신규 3)

- [ ] **Step 7: 커밋**

```
git add src/explorer.py tests/test_explorer.py
git commit -m "feat: explorer main() restart loop with watchdog integration"
```
