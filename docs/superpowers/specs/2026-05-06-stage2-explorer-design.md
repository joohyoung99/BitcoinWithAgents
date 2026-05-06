# Stage 2 Design — 탐색 에이전트 (Explorer Agent)

**날짜:** 2026-05-06  
**범위:** 2단계 — 매크로/ETF/심리/시장 데이터 수집 + Gemini 2.5 Pro 분석 + Risk-On/Off 판단  
**완료 기준:** `uv run python -m src.explorer` 실행 시 1시간마다 `data/explorer_report.json` 갱신

---

## 선택한 접근법

**dataclasses + 파이프라인** 구조. 서버에서 일주일 이상 안정적으로 운영해야 하므로 타입이 명확한 dataclass로 각 수집기 출력을 정의하고, 파이프라인 흐름을 명시적으로 유지.

APScheduler로 1시간 간격 자동 실행. 수집기 실패는 해당 항목만 None 처리하고 계속 진행 (프로세스 죽지 않음).

---

## 파일 구조

```
src/
├── client.py                  # (기존) Bitget 클라이언트
├── account.py                 # (기존) 잔고 조회
├── models.py                  # 신규 — 공유 dataclasses
└── explorer.py                # 신규 — 수집 + Gemini + 스케줄러

data/
└── explorer_report.json       # 출력 파일 (자동 생성, 매 사이클 덮어쓰기)

tests/
├── test_client.py             # (기존)
├── test_account.py            # (기존)
└── test_explorer.py           # 신규
```

`data/explorer_report.json`은 Stage 3 (차트 에이전트)와 Stage 4 (Regime Classifier)가 읽는 **공유 상태 파일**. 실시간 에이전트는 매 사이클마다 이 파일을 읽어 현재 리스크 상태를 확인한다.

---

## 데이터 모델 (`src/models.py`)

```python
from dataclasses import dataclass

@dataclass
class MacroData:
    fed_funds_rate: float       # FRED DFF 시리즈 최신값
    rate_trend: str             # "hiking" | "holding" | "cutting"

@dataclass
class ETFData:
    net_flow_3d: float          # 3일 평균 순유입 (USD)
    flow_signal: str            # "inflow" | "outflow" | "neutral"

@dataclass
class FearGreedData:
    score: int                  # 0~100
    label: str                  # "Extreme Fear" ... "Extreme Greed"

@dataclass
class MarketData:
    funding_rate: float         # % (예: 0.01)
    open_interest: float        # USD
    long_short_ratio: float     # long / short 비율

@dataclass
class ExplorerReport:
    timestamp: str              # ISO 8601
    risk: str                   # "risk-on" | "risk-off"
    summary: str                # Gemini 분석 요약 (한국어)
    macro: MacroData | None
    etf: ETFData | None
    fear_greed: FearGreedData | None
    market: MarketData | None
```

---

## 수집 함수 (`src/explorer.py`)

### `collect_macro() -> MacroData | None`
- FRED REST API: `https://api.stlouisfed.org/fred/series/observations?series_id=DFF&api_key=KEY&sort_order=desc&limit=5&file_type=json`
- `FRED_API_KEY` 환경변수 사용
- DFF 최근값으로 금리 수준 파악, 최근 3개 값 비교해 trend 계산

### `collect_etf() -> ETFData | None`
- SoSoValue API BTC ETF 자금흐름 최근 3일 (정확한 엔드포인트는 구현 시 SoSoValue 문서 확인)
- `SOSOVALUE_API_KEY` 환경변수 사용
- 3일 평균 순유입 양수 → "inflow", 음수 → "outflow", ±1M USD 이내 → "neutral"

### `collect_fear_greed() -> FearGreedData | None`
- `https://api.alternative.me/fng/` — 키 없음, 단순 GET
- `score`, `value_classification` 파싱

### `collect_market() -> MarketData | None`
- Bitget 공개 API (인증 불필요):
  - `GET /api/v2/mix/market/current-fund-rate?symbol=BTCUSDT&productType=USDT-FUTURES`
  - `GET /api/v2/mix/market/open-interest?symbol=BTCUSDT&productType=USDT-FUTURES`
  - `GET /api/v2/mix/market/account-long-short-pos-ratio?symbol=BTCUSDT&productType=USDT-FUTURES&period=1h`

---

## Gemini 분석 (`analyse`)

```python
def analyse(
    macro: MacroData | None,
    etf: ETFData | None,
    fear_greed: FearGreedData | None,
    market: MarketData | None,
) -> ExplorerReport
```

- `google-genai` SDK, `gemini-2.5-pro` 모델
- **ADC (Application Default Credentials)** 인증 — `api_key` 없이 `genai.Client()` 초기화
  - 로컬: `gcloud auth application-default login`
  - 서버: `GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json` 환경변수
- 프롬프트에 수집된 데이터 전달 (None인 항목은 "수집 실패"로 표시)
- Risk-On 조건 (Voting — 2개 이상 충족):
  - Fear & Greed > 60
  - ETF 순유입 > 0 (3일 평균)
  - Funding Rate 정상 범위 (0 ~ 0.01%)
  - 매크로 금리 동결/인하 기조
- Risk-Off 조건 (1개라도 충족 시 즉시 발동):
  - Fear & Greed < 30
  - ETF 자금 순유출 3일 연속
  - Funding Rate 극단값 (> 0.1% or < -0.05%)
  - 기타 이상 신호
- Gemini 응답은 JSON 형식으로 `{"risk": "risk-on"|"risk-off", "summary": "..."}` 요청

---

## 파이프라인 (`run_once`)

```
run_once()
├── collect_macro()       → MacroData | None
├── collect_etf()         → ETFData | None
├── collect_fear_greed()  → FearGreedData | None
├── collect_market()      → MarketData | None
├── analyse(...)          → ExplorerReport   ← Gemini 2.5 Pro
└── save_report(report)   → data/explorer_report.json 덮어쓰기
```

---

## 스케줄러 (`main`)

```python
def main():
    run_once()   # 시작 즉시 1회 실행
    scheduler = BlockingScheduler()
    scheduler.add_job(run_once, "interval", hours=1)
    scheduler.start()
```

실행: `uv run python -m src.explorer`

---

## 에러 처리 원칙

| 상황 | 처리 |
|------|------|
| 수집기 1~3개 실패 | None 전달, 나머지로 Gemini 분석 계속 |
| 수집기 전부 실패 | Gemini 호출 스킵, 이전 report.json risk 유지 |
| Gemini 실패 | 이전 report.json risk 유지, 사이클 스킵 |
| 전체 예외 | 로그 출력 후 다음 사이클 대기 (프로세스 유지) |

---

## 환경변수 추가 (`.env`)

```
FRED_API_KEY=
SOSOVALUE_API_KEY=
# Gemini: API KEY 없이 ADC 인증 사용
# 로컬 → gcloud auth application-default login
# 서버 → GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

---

## 추가 패키지

```
google-genai
apscheduler
```

---

## 테스트 전략 (`tests/test_explorer.py`)

| 테스트 | 검증 내용 |
|--------|-----------|
| `test_collect_fear_greed_returns_data` | Alternative.me mock → FearGreedData 파싱 |
| `test_collect_market_returns_data` | Bitget mock → MarketData 파싱 |
| `test_analyse_returns_risk_on` | Gemini mock (risk-on JSON) → report.risk == "risk-on" |
| `test_analyse_returns_risk_off` | Gemini mock (risk-off JSON) → report.risk == "risk-off" |
| `test_run_once_writes_json` | 파일 생성 및 내용 검증 |
| `test_run_once_survives_collector_failure` | 수집기 1개 None 반환 시 예외 없이 완료 |

---

## 이 설계가 이후 단계에 미치는 영향

- `src/models.py`의 dataclass는 Stage 3~8 전체에서 공유 사용
- `data/explorer_report.json`은 Stage 3 (chart.py), Stage 4 (regime.py)가 읽는 공유 상태
- 실시간 에이전트(Stage 3)는 15분봉 사이클마다 이 파일의 `risk` 필드를 참조
