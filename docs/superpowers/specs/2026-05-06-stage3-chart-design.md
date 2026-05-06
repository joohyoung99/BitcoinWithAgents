# Stage 3 Design — 차트 분석 에이전트 (Chart Analysis Agent)

**날짜:** 2026-05-06
**범위:** 3단계 — 15분봉 기술 지표 계산 + 룰 기반 Trend/Range 판단 + Gemini Flash 신뢰도 필터
**완료 기준:** `data/state.json`에 15분마다 차트 신호(entry_signal, confidence) 갱신

---

## 선택한 접근법

**단일 파일 `src/chart.py`, 내부 6-Layer 함수 분리.**

pandas-ta로 지표 계산, 룰 기반 판단이 주(Primary), Gemini 2.5 Flash는 confidence 점수(0~100) 보조로만 사용. confidence < 60이면 entry_signal 억제. Stage 2 explorer.py 스케줄러에 통합 (단일 프로세스), 5분 offset으로 explorer → chart 실행 순서 보장.

---

## 파일 구조

```
src/
├── models.py      # ChartSignal dataclass 추가
├── chart.py       # 신규 — 6-Layer 단일 파일
└── explorer.py    # main() 수정 — chart 스케줄러 등록

data/
├── explorer_report.json   # (기존) 탐색 에이전트 출력
└── state.json             # 신규 — 심볼 단위 통합 상태 파일
```

---

## 데이터 모델 추가 (`src/models.py`)

```python
@dataclass
class ChartSignal:
    symbol: str              # "BTCUSDT"
    updated_at: str          # ISO 8601
    trend_range: str         # "trend" | "range"
    adx: float
    rsi: float
    ema_aligned: bool        # EMA20 > EMA50 > EMA200
    ema50_slope: float       # 양수=상승, 음수=하락
    atr: float
    entry_signal: str        # "long" | "short" | "none"
    confidence: int          # 0~100 (Gemini Flash 점수)
    signal_summary: str      # 룰 판단 + LLM 코멘트
```

---

## state.json 구조 (심볼 단위)

```json
{
  "BTCUSDT": {
    "updated_at": "2026-05-06T12:05:00+00:00",
    "risk": "risk-on",
    "risk_summary": "Fear & Greed 72, ETF 순유입",
    "trend_range": "trend",
    "adx": 28.5,
    "rsi": 52.3,
    "ema_aligned": true,
    "ema50_slope": 0.0023,
    "atr": 1250.5,
    "entry_signal": "long",
    "confidence": 78,
    "signal_summary": "EMA 정배열 + 눌림목 확인, Gemini 동의"
  }
}
```

---

## 6-Layer 내부 구조 (`src/chart.py`)

### Layer 1 — 데이터 수집
```python
fetch_candles(client: BitgetAPI, interval: str = "15m", limit: int = 500) -> pd.DataFrame
```
- Bitget bitpy SDK: `client.market.get_candles("BTCUSDT_UMCBL", interval, limit)`
- 컬럼: timestamp, open, high, low, close, volume (float 변환)
- `df = df.iloc[:-1]` — 미완성 마지막 캔들 제거
- 반환 전 시간 오름차순 정렬

### Layer 2 — 지표 계산
```python
calc_indicators(df: pd.DataFrame) -> pd.DataFrame
```
- pandas-ta로 계산:
  - EMA(20), EMA(50), EMA(200)
  - ADX(14) → `ADX_14` 컬럼
  - RSI(14) → `RSI_14`
  - MACD(12,26,9) → `MACD_12_26_9`, `MACDh_12_26_9`, `MACDs_12_26_9`
  - BBands(20,2) → `BBL_20_2.0`, `BBM_20_2.0`, `BBU_20_2.0`
  - ATR(14) → `ATRr_14`
  - OBV → `OBV`
- EMA50 slope: `(ema50[-1] - ema50[-4]) / ema50[-4]` (최근 3봉 변화율)
- `df = df.dropna()` — 워밍업 기간 NaN 제거

### Layer 3 — 룰 기반 판단
```python
determine_trend_range(df: pd.DataFrame, prev_trend_range: str) -> str
```
히스테리시스 적용 (CLAUDE.md 기준):
- Trend 진입: `ADX > 25 AND ema50_slope > 0`
- Trend 유지 (prev="trend"): `ADX > 22`
- Range 진입: `ADX < 20`
- 20 ≤ ADX ≤ 22 구간: `prev_trend_range` 그대로 유지

```python
determine_entry(df: pd.DataFrame, trend_range: str, risk: str) -> str
```
CLAUDE.md 전략 그대로:
- **Normal (trend + risk-on):** EMA 정배열 + EMA20~50 사이 눌림목 + RSI 40~60 → "long" / 역배열 → "short"
- **Caution (range + risk-on):** BB 상단 터치 → "short" / BB 하단 터치 → "long"
- **Risk-Off Trend:** 롱 완전 금지, 숏만 허용
- **Halt (range + risk-off):** "none" 강제
- Risk-Off이면 롱 완전 금지

### Layer 4 — Gemini Flash 신뢰도
```python
score_signal(df: pd.DataFrame, signal: str, trend_range: str) -> tuple[int, str]
```
- `genai.Client()` (ADC, api_key 없음), `gemini-2.5-flash` 모델
- 입력: 최근 10봉 지표 수치 요약 + 룰 기반 신호
- 출력: `(confidence: int, comment: str)`
- **Fallback:** Gemini 실패 시 `(50, "LLM unavailable")` 반환 (예외 삼킴)
- confidence < 60 → `run_chart_once`에서 entry_signal="none" 억제

### Layer 5 — 저장 (Atomic Write)
```python
update_state(signal: ChartSignal) -> None
```
- 기존 state.json 읽어 심볼 키 갱신 (다른 심볼 데이터 보존)
- explorer_report.json에서 risk/risk_summary 복사
- Atomic write: `.tmp` 파일 기록 후 `rename()`

### Layer 6 — 진입점
```python
run_chart_once() -> None
```
- 전체 try/except 래핑
- 연속 실패 차단: 모듈 레벨 `_consecutive_failures` 카운터, ≥3회면 entry_signal="none" 강제

---

## Stale 처리

| 조건 | 처리 |
|------|------|
| state.json `updated_at` > 30분 전 | entry_signal="none" 강제 |
| explorer_report.json `timestamp` > 2시간 전 | risk="risk-off" fallback |
| state.json 파일 없음 | trend_range="range", risk="risk-off" (보수적 기본값) |

```python
STALE_CHART_MIN = 30
STALE_EXPLORER_MIN = 120
```

---

## Fallback 요약

| 상황 | Fallback |
|------|---------|
| Bitget API 실패 | 이전 state.json 유지, 사이클 스킵 |
| pandas-ta 계산 오류 | 로그 출력, 스킵 |
| Gemini 실패 | confidence=50, signal 유지 |
| state.json 없음 | trend_range="range", risk="risk-off" |
| 연속 3회 실패 | entry_signal="none" 강제 + 로그 |
| 전체 예외 | 로그 출력, 프로세스 유지 |

---

## 스케줄러 통합 (`src/explorer.py` main() 수정)

```python
from src.chart import run_chart_once
from datetime import datetime, UTC, timedelta

def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    run_once()          # explorer 즉시 실행
    run_chart_once()    # chart 즉시 실행

    now = datetime.now(UTC)
    scheduler = BlockingScheduler()
    scheduler.add_job(run_once,        "interval", hours=1,    id="explorer")
    scheduler.add_job(run_chart_once,  "interval", minutes=15,
                      start_date=now + timedelta(minutes=5),   id="chart")
    # explorer :00 → chart :05, :20, :35, :50
    scheduler.start()
```

---

## 에러 처리

| 상황 | 처리 |
|------|------|
| Bitget 캔들 API 실패 | 이전 state.json 유지, 사이클 스킵 |
| pandas-ta 계산 오류 | 로그 출력 후 스킵 |
| Gemini 실패 | confidence=50 fallback |
| state.json 읽기 실패 | 보수적 기본값 적용 |
| 연속 3회 실패 | entry_signal="none" 강제 |
| 전체 예외 | 로그 출력, 프로세스 유지 |

---

## 추가 패키지

```
pandas
pandas-ta
```

---

## 테스트 전략 (`tests/test_chart.py`)

| 테스트 | 검증 내용 |
|--------|-----------|
| `test_calc_indicators_adds_columns` | EMA/ADX/RSI/ATR 컬럼 존재 확인 |
| `test_calc_indicators_drops_last_candle` | `df.iloc[:-1]` 적용 확인 |
| `test_determine_trend_range_trend` | ADX>25, slope>0 → "trend" |
| `test_determine_trend_range_hysteresis` | ADX 22~25 구간: 이전 상태 유지 |
| `test_determine_trend_range_range` | ADX<20 → "range" |
| `test_determine_entry_blocks_long_on_risk_off` | risk-off시 롱 → "none" |
| `test_determine_entry_halt` | range + risk-off → "none" |
| `test_score_signal_suppresses_below_60` | confidence<60 → entry_signal="none" |
| `test_score_signal_fallback_on_gemini_fail` | Gemini 예외 → confidence=50 |
| `test_run_chart_once_writes_state_json` | 파이프라인 전체 + atomic write 확인 |
| `test_run_chart_once_consecutive_failure_block` | 3회 연속 실패 → "none" |

---

## 이 설계가 이후 단계에 미치는 영향

- `data/state.json`은 Stage 4 (Regime Classifier)가 읽는 공유 상태
- `ChartSignal` dataclass는 Stage 4에서 직접 사용
- `explorer.py main()` 수정으로 Stage 2 단독 실행 → Stage 2+3 통합 실행으로 전환
