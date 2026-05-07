# Stage 6: 내결함성 (Watchdog + 상태 복구) Design

## Goal

메인 스케줄러가 멈추거나 heartbeat가 끊겼을 때 자동으로 재시작하고, 3회 재시작 실패 시 프로세스를 종료하는 Watchdog 스레드를 구현한다.

## Architecture

두 파일만 변경한다.

- **`src/watchdog.py`** (신규): 모듈 수준 `beat()` + `start(scheduler)` 함수
- **`src/explorer.py`** (수정): `main()` 재시작 루프 + 각 잡 앞에 `beat()` 삽입

상태 복구(state.json / positions.json / daily.json)는 이미 각 `run_*_once()` 함수가 시작 시 파일을 읽으므로 추가 코드 없이 자동 복구된다.

## Tech Stack

Python threading, APScheduler BlockingScheduler (기존 유지)

---

## src/watchdog.py

```
전역 상태:
  _last_beat: float = time.time()   # 마지막 heartbeat 타임스탬프
  _lock: threading.Lock             # 스레드 안전 갱신

공개 함수:
  beat() -> None
    - _last_beat = time.time()

  start(scheduler, stale_minutes: int = 20) -> None
    - 데몬 스레드(_loop)를 시작
    - _loop: 5분(300s)마다 체크
        age = time.time() - _last_beat
        if age > stale_minutes * 60:
            print stale 경고
            scheduler.shutdown(wait=False)
            return  # watchdog 스레드 종료
```

## src/explorer.py main() 변경

```
현재: BlockingScheduler 한 번 시작, 블로킹
변경: while 재시작 루프

def _job_explorer():
    beat()
    run_once()

def _job_chain():
    beat()
    run_chart_once()
    run_regime_once()
    run_executor_once()

def main():
    # ws_monitor 데몬 스레드 시작 (기존 유지)

    restart_count = 0
    MAX_RESTARTS = 3

    while True:
        beat()                          # 초기 heartbeat
        now = datetime.now(UTC)
        scheduler = BlockingScheduler()
        scheduler.add_job(_job_explorer, "interval", hours=1, id="explorer")
        scheduler.add_job(
            _job_chain, "interval", minutes=15,
            start_date=now + timedelta(minutes=5),
            id="chart_regime_executor",
            misfire_grace_time=60,
        )
        watchdog.start(scheduler, stale_minutes=20)

        print("[explorer] starting — running once immediately")
        _job_explorer()
        _job_chain()

        try:
            scheduler.start()           # blocks
        except (KeyboardInterrupt, SystemExit):
            print("[explorer] shutdown")
            return

        # scheduler.shutdown() 호출됨 (watchdog 또는 예외)
        restart_count += 1
        if restart_count >= MAX_RESTARTS:
            print(f"[watchdog] {MAX_RESTARTS} restarts exhausted — exiting")
            sys.exit(1)

        print(f"[explorer] restarting (attempt {restart_count})")
        time.sleep(5)
```

## Watchdog Stale 기준

- **Stale 임계값**: 20분 (1200초)
- **체크 주기**: 5분 (300초)
- **근거**: 15분 스케줄 인터벌 + 5분 버퍼. 정상 실행 중 잡 하나가 늦어도 false positive 없음.

## 재시작 로직

```
1회 ~ 2회: 새 BlockingScheduler + 새 watchdog 스레드 생성 후 재시작
3회 도달: sys.exit(1) — 외부 프로세스 매니저 또는 수동 재시작 필요
```

## Error Handling

- `scheduler.shutdown()` 실패 시 예외 무시 후 watchdog 스레드 종료 (main 루프는 다음 반복으로 넘어감)
- `KeyboardInterrupt` / `SystemExit`: 정상 종료로 처리, 재시작 루프 탈출

## Testing

**`tests/test_watchdog.py`**:

1. `beat()` 직후 `start()` — stale 감지 전에 체크 타이머가 만료되지 않음 → `shutdown()` 호출 안 됨
2. beat 없이 stale_minutes=0 (또는 매우 작은 값) → watchdog가 `shutdown()` 호출
3. `main()` 재시작 루프 — scheduler가 즉시 shutdown될 때 `restart_count` 증가 확인
4. `restart_count >= 3` → `sys.exit(1)` 호출 확인 (`pytest.raises(SystemExit)`)
