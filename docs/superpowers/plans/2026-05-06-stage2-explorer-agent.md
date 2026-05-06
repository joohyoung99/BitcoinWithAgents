# Stage 2: Explorer Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매크로/ETF/심리/시장 데이터를 1시간마다 수집하고 Gemini 2.5 Pro로 Risk-On/Off를 판단해 `data/explorer_report.json`에 저장하는 Explorer Agent를 구현한다.

**Architecture:** Dataclasses 파이프라인 — 4개의 독립 수집기(FRED, SoSoValue, Alternative.me, Bitget 공개 API)가 Gemini 2.5 Pro 분석 호출로 연결된다. 각 수집기는 독립적인 try/except로 감싸져 있어 실패해도 사이클이 중단되지 않는다. APScheduler가 1시간 간격으로 파이프라인을 실행. Gemini 인증은 ADC (Application Default Credentials) 사용.

**Tech Stack:** `google-genai` (ADC), `apscheduler`, `requests` (기설치), `python-dotenv`

---

## File Map

| 파일 | 액션 | 역할 |
|------|------|------|
| `src/models.py` | 신규 생성 | 공유 dataclasses (MacroData, ETFData, FearGreedData, MarketData, ExplorerReport) |
| `src/explorer.py` | 신규 생성 | 수집기 4개 + Gemini analyse + run_once + main (APScheduler) |
| `tests/test_explorer.py` | 신규 생성 | 단위 테스트 12개 |
| `pyproject.toml` | 수정 | google-genai, apscheduler 추가 |
| `.env.example` | 수정 | FRED_API_KEY, SOSOVALUE_API_KEY, ADC 안내 추가 |

---

### Task 1: 의존성 설치

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`

- [ ] **Step 1: 패키지 추가**

```bash
uv add "google-genai" "apscheduler"
```

Expected: `pyproject.toml`에 `google-genai`, `apscheduler` 추가됨

- [ ] **Step 2: 임포트 확인**

```bash
uv run python -c "from google import genai; from apscheduler.schedulers.blocking import BlockingScheduler; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: .env.example 업데이트**

`.env.example`의 기존 내용에 아래 항목 추가:

```
FRED_API_KEY=
SOSOVALUE_API_KEY=
# Gemini: API KEY 없이 ADC 인증 사용
# 로컬 → gcloud auth application-default login
# 서버 → GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

- [ ] **Step 4: 커밋**

```bash
git add pyproject.toml uv.lock .env.example
git commit -m "chore: add google-genai, apscheduler dependencies"
```

---

### Task 2: 데이터 모델 (`src/models.py`)

**Files:**
- Create: `src/models.py`

- [ ] **Step 1: src/models.py 생성**

```python
from dataclasses import dataclass


@dataclass
class MacroData:
    fed_funds_rate: float
    rate_trend: str  # "hiking" | "holding" | "cutting"


@dataclass
class ETFData:
    net_flow_3d: float
    flow_signal: str  # "inflow" | "outflow" | "neutral"


@dataclass
class FearGreedData:
    score: int
    label: str


@dataclass
class MarketData:
    funding_rate: float
    open_interest: float
    long_short_ratio: float


@dataclass
class ExplorerReport:
    timestamp: str
    risk: str  # "risk-on" | "risk-off"
    summary: str
    macro: MacroData | None
    etf: ETFData | None
    fear_greed: FearGreedData | None
    market: MarketData | None
```

- [ ] **Step 2: 임포트 확인**

```bash
uv run python -c "from src.models import ExplorerReport, MacroData; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add src/models.py
git commit -m "feat: add shared dataclasses for explorer pipeline"
```

---

### Task 3: Fear & Greed 수집기

**Files:**
- Create: `src/explorer.py` (스켈레톤 + collect_fear_greed)
- Create: `tests/test_explorer.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_explorer.py` 생성:

```python
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.models import (
    ETFData,
    ExplorerReport,
    FearGreedData,
    MacroData,
    MarketData,
)


def test_collect_fear_greed_returns_data():
    from src.explorer import collect_fear_greed

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [{"value": "45", "value_classification": "Fear"}]
    }
    with patch("src.explorer.requests.get", return_value=mock_resp):
        result = collect_fear_greed()

    assert isinstance(result, FearGreedData)
    assert result.score == 45
    assert result.label == "Fear"


def test_collect_fear_greed_returns_none_on_error():
    from src.explorer import collect_fear_greed

    with patch("src.explorer.requests.get", side_effect=Exception("timeout")):
        result = collect_fear_greed()

    assert result is None
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/test_explorer.py::test_collect_fear_greed_returns_data -v
```

Expected: `ERROR` — `src.explorer` 모듈 없음

- [ ] **Step 3: src/explorer.py 스켈레톤 + collect_fear_greed 구현**

```python
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import requests
from google import genai

from src.models import (
    ETFData,
    ExplorerReport,
    FearGreedData,
    MacroData,
    MarketData,
)

FEAR_GREED_URL = "https://api.alternative.me/fng/"
BITGET_BASE = "https://api.bitget.com"
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
SOSOVALUE_BASE = "https://api.sosovalue.com"  # SoSoValue 문서 확인 후 조정

REPORT_PATH = Path("data/explorer_report.json")


def collect_fear_greed() -> FearGreedData | None:
    try:
        resp = requests.get(FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        item = resp.json()["data"][0]
        return FearGreedData(
            score=int(item["value"]),
            label=item["value_classification"],
        )
    except Exception as e:
        print(f"[explorer] fear_greed collection failed: {e}")
        return None
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_explorer.py::test_collect_fear_greed_returns_data tests/test_explorer.py::test_collect_fear_greed_returns_none_on_error -v
```

Expected: 2 PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/explorer.py tests/test_explorer.py
git commit -m "feat: add collect_fear_greed and explorer skeleton"
```

---

### Task 4: Bitget 시장 데이터 수집기

**Files:**
- Modify: `src/explorer.py`
- Modify: `tests/test_explorer.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_explorer.py`에 추가:

```python
def test_collect_market_returns_data():
    from src.explorer import collect_market

    def mock_get(url, params=None, timeout=None):
        mock = MagicMock()
        if "current-fund-rate" in url:
            mock.json.return_value = {"code": "00000", "data": {"fundingRate": "0.0001"}}
        elif "open-interest" in url:
            mock.json.return_value = {
                "code": "00000",
                "data": {"openInterestList": [{"size": "1234567890"}]},
            }
        elif "long-short" in url:
            mock.json.return_value = {
                "code": "00000",
                "data": [{"longShortRatio": "1.25"}],
            }
        return mock

    with patch("src.explorer.requests.get", side_effect=mock_get):
        result = collect_market()

    assert isinstance(result, MarketData)
    assert result.funding_rate == pytest.approx(0.0001)
    assert result.open_interest == pytest.approx(1_234_567_890.0)
    assert result.long_short_ratio == pytest.approx(1.25)


def test_collect_market_returns_none_on_error():
    from src.explorer import collect_market

    with patch("src.explorer.requests.get", side_effect=Exception("network error")):
        result = collect_market()

    assert result is None
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/test_explorer.py::test_collect_market_returns_data -v
```

Expected: `AttributeError` — collect_market 미정의

- [ ] **Step 3: collect_market 구현 (src/explorer.py에 추가)**

```python
def collect_market() -> MarketData | None:
    try:
        symbol = "BTCUSDT"
        product = "USDT-FUTURES"

        fr = requests.get(
            f"{BITGET_BASE}/api/v2/mix/market/current-fund-rate",
            params={"symbol": symbol, "productType": product},
            timeout=10,
        )
        fr.raise_for_status()
        funding_rate = float(fr.json()["data"]["fundingRate"])

        oi = requests.get(
            f"{BITGET_BASE}/api/v2/mix/market/open-interest",
            params={"symbol": symbol, "productType": product},
            timeout=10,
        )
        oi.raise_for_status()
        open_interest = float(oi.json()["data"]["openInterestList"][0]["size"])

        ls = requests.get(
            f"{BITGET_BASE}/api/v2/mix/market/account-long-short-pos-ratio",
            params={"symbol": symbol, "productType": product, "period": "1h"},
            timeout=10,
        )
        ls.raise_for_status()
        ls_data = ls.json()["data"]
        long_short_ratio = float(ls_data[0]["longShortRatio"]) if ls_data else 1.0

        return MarketData(
            funding_rate=funding_rate,
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
        )
    except Exception as e:
        print(f"[explorer] market collection failed: {e}")
        return None
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_explorer.py::test_collect_market_returns_data tests/test_explorer.py::test_collect_market_returns_none_on_error -v
```

Expected: 2 PASSED

- [ ] **Step 5: 실제 API 필드명 검증**

```bash
uv run python -c "
import requests
r = requests.get('https://api.bitget.com/api/v2/mix/market/current-fund-rate', params={'symbol': 'BTCUSDT', 'productType': 'USDT-FUTURES'})
print('funding-rate:', r.json())
r2 = requests.get('https://api.bitget.com/api/v2/mix/market/open-interest', params={'symbol': 'BTCUSDT', 'productType': 'USDT-FUTURES'})
print('open-interest:', r2.json())
r3 = requests.get('https://api.bitget.com/api/v2/mix/market/account-long-short-pos-ratio', params={'symbol': 'BTCUSDT', 'productType': 'USDT-FUTURES', 'period': '1h'})
print('long-short:', r3.json())
"
```

실제 응답에서 필드명이 다르면 `collect_market`과 테스트 mock을 함께 수정.

- [ ] **Step 6: 커밋**

```bash
git add src/explorer.py tests/test_explorer.py
git commit -m "feat: add collect_market for Bitget public API"
```

---

### Task 5: FRED 매크로 수집기

**Files:**
- Modify: `src/explorer.py`
- Modify: `tests/test_explorer.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_explorer.py`에 추가:

```python
def test_collect_macro_returns_data():
    from src.explorer import collect_macro

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "observations": [
            {"value": "5.33", "date": "2024-03-01"},
            {"value": "5.33", "date": "2024-02-01"},
            {"value": "5.08", "date": "2024-01-01"},
        ]
    }

    with patch.dict(os.environ, {"FRED_API_KEY": "testkey"}), \
         patch("src.explorer.requests.get", return_value=mock_resp):
        result = collect_macro()

    assert isinstance(result, MacroData)
    assert result.fed_funds_rate == pytest.approx(5.33)
    assert result.rate_trend == "hiking"


def test_collect_macro_returns_none_when_no_key():
    from src.explorer import collect_macro

    with patch.dict(os.environ, {"FRED_API_KEY": ""}, clear=False):
        result = collect_macro()

    assert result is None
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/test_explorer.py::test_collect_macro_returns_data -v
```

Expected: `AttributeError` — collect_macro 미정의

- [ ] **Step 3: collect_macro 구현 (src/explorer.py에 추가)**

```python
def collect_macro() -> MacroData | None:
    try:
        api_key = os.getenv("FRED_API_KEY", "")
        if not api_key:
            print("[explorer] FRED_API_KEY not set, skipping macro")
            return None

        resp = requests.get(
            FRED_BASE,
            params={
                "series_id": "DFF",
                "api_key": api_key,
                "sort_order": "desc",
                "limit": "5",
                "file_type": "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        obs = [o for o in resp.json()["observations"] if o["value"] != "."]
        if not obs:
            return None

        values = [float(o["value"]) for o in obs]
        current = values[0]
        if len(values) >= 2:
            trend = (
                "hiking" if values[0] > values[-1]
                else "cutting" if values[0] < values[-1]
                else "holding"
            )
        else:
            trend = "holding"

        return MacroData(fed_funds_rate=current, rate_trend=trend)
    except Exception as e:
        print(f"[explorer] macro collection failed: {e}")
        return None
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_explorer.py::test_collect_macro_returns_data tests/test_explorer.py::test_collect_macro_returns_none_when_no_key -v
```

Expected: 2 PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/explorer.py tests/test_explorer.py
git commit -m "feat: add collect_macro from FRED API"
```

---

### Task 6: SoSoValue ETF 수집기

**Files:**
- Modify: `src/explorer.py`
- Modify: `tests/test_explorer.py`

> **주의:** SoSoValue API의 정확한 엔드포인트와 응답 구조는 SoSoValue 개발자 포털에서 확인 후 Step 1에서 조정. 아래 코드는 `data.list[].netFlow` 구조를 가정함.

- [ ] **Step 1: SoSoValue API 엔드포인트 확인**

SoSoValue 개발자 포털 로그인 후 아래 항목 확인:
- Base URL
- 인증 방식 (Bearer token? API key 헤더?)
- BTC ETF 일별 순유입 엔드포인트
- 응답 JSON 구조 (특히 날짜별 netFlow 필드명)

`SOSOVALUE_BASE`와 이후 코드의 엔드포인트/필드명을 실제 값으로 조정.

- [ ] **Step 2: 실패하는 테스트 추가**

`tests/test_explorer.py`에 추가 (Step 1에서 확인한 실제 필드명으로 mock 조정):

```python
def test_collect_etf_returns_data():
    from src.explorer import collect_etf

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "list": [
                {"date": "2024-03-03", "netFlow": 500_000_000},
                {"date": "2024-03-02", "netFlow": 300_000_000},
                {"date": "2024-03-01", "netFlow": -100_000_000},
            ]
        }
    }

    with patch.dict(os.environ, {"SOSOVALUE_API_KEY": "testkey"}), \
         patch("src.explorer.requests.get", return_value=mock_resp):
        result = collect_etf()

    assert isinstance(result, ETFData)
    assert result.flow_signal == "inflow"
    assert result.net_flow_3d == pytest.approx(233_333_333.33, rel=1e-3)


def test_collect_etf_returns_none_when_no_key():
    from src.explorer import collect_etf

    with patch.dict(os.environ, {"SOSOVALUE_API_KEY": ""}, clear=False):
        result = collect_etf()

    assert result is None
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/test_explorer.py::test_collect_etf_returns_data -v
```

Expected: `AttributeError` — collect_etf 미정의

- [ ] **Step 4: collect_etf 구현 (src/explorer.py에 추가)**

```python
def collect_etf() -> ETFData | None:
    try:
        api_key = os.getenv("SOSOVALUE_API_KEY", "")
        if not api_key:
            print("[explorer] SOSOVALUE_API_KEY not set, skipping ETF")
            return None

        # SoSoValue 문서 확인 후 엔드포인트/헤더/파라미터 조정
        resp = requests.get(
            f"{SOSOVALUE_BASE}/v1/fund/bitcoin-spot-etf/fund-flow",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"days": 3},
            timeout=10,
        )
        resp.raise_for_status()
        flows = resp.json()["data"]["list"][:3]

        net_flow_3d = sum(item["netFlow"] for item in flows) / len(flows)
        if net_flow_3d > 1_000_000:
            signal = "inflow"
        elif net_flow_3d < -1_000_000:
            signal = "outflow"
        else:
            signal = "neutral"

        return ETFData(net_flow_3d=net_flow_3d, flow_signal=signal)
    except Exception as e:
        print(f"[explorer] ETF collection failed: {e}")
        return None
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/test_explorer.py::test_collect_etf_returns_data tests/test_explorer.py::test_collect_etf_returns_none_when_no_key -v
```

Expected: 2 PASSED

- [ ] **Step 6: 커밋**

```bash
git add src/explorer.py tests/test_explorer.py
git commit -m "feat: add collect_etf from SoSoValue API"
```

---

### Task 7: Gemini 분석 함수

**Files:**
- Modify: `src/explorer.py`
- Modify: `tests/test_explorer.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_explorer.py`에 추가:

```python
def test_analyse_returns_risk_on():
    from src.explorer import analyse

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"risk": "risk-on", "summary": "시장 상태 양호"}'
    mock_client.models.generate_content.return_value = mock_response

    fg = FearGreedData(score=70, label="Greed")
    market = MarketData(funding_rate=0.005, open_interest=1e9, long_short_ratio=1.2)

    with patch("src.explorer.genai.Client", return_value=mock_client):
        report = analyse(None, None, fg, market)

    assert report.risk == "risk-on"
    assert report.summary == "시장 상태 양호"
    assert report.fear_greed is fg
    assert report.market is market
    assert report.macro is None


def test_analyse_returns_risk_off():
    from src.explorer import analyse

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"risk": "risk-off", "summary": "위험 신호 감지"}'
    mock_client.models.generate_content.return_value = mock_response

    fg = FearGreedData(score=20, label="Extreme Fear")
    market = MarketData(funding_rate=-0.06, open_interest=8e8, long_short_ratio=0.8)

    with patch("src.explorer.genai.Client", return_value=mock_client):
        report = analyse(None, None, fg, market)

    assert report.risk == "risk-off"
    assert report.summary == "위험 신호 감지"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/test_explorer.py::test_analyse_returns_risk_on -v
```

Expected: `AttributeError` — analyse 미정의

- [ ] **Step 3: _build_prompt + analyse 구현 (src/explorer.py에 추가)**

```python
def _build_prompt(
    macro: MacroData | None,
    etf: ETFData | None,
    fear_greed: FearGreedData | None,
    market: MarketData | None,
) -> str:
    lines = [
        "당신은 BTC/USDT 선물 트레이딩 AI 에이전트입니다.",
        "아래 데이터를 분석하여 현재 시장 상태가 Risk-On인지 Risk-Off인지 판단하세요.",
        "",
        "## 수집 데이터",
    ]
    if macro:
        lines += [
            "### 매크로 (FRED)",
            f"- 연준 기준금리: {macro.fed_funds_rate}%",
            f"- 금리 추세: {macro.rate_trend}",
        ]
    else:
        lines.append("### 매크로 (FRED): 수집 실패")

    if etf:
        lines += [
            "### ETF 자금흐름 (SoSoValue)",
            f"- 3일 평균 순유입: {etf.net_flow_3d:,.0f} USD",
            f"- 신호: {etf.flow_signal}",
        ]
    else:
        lines.append("### ETF 자금흐름: 수집 실패")

    if fear_greed:
        lines += [
            "### Fear & Greed Index",
            f"- 점수: {fear_greed.score}/100",
            f"- 상태: {fear_greed.label}",
        ]
    else:
        lines.append("### Fear & Greed: 수집 실패")

    if market:
        lines += [
            "### 시장 데이터 (Bitget)",
            f"- 펀딩 레이트: {market.funding_rate:.4f}%",
            f"- 미결제약정(OI): {market.open_interest:,.0f} USD",
            f"- 롱숏 비율: {market.long_short_ratio:.2f}",
        ]
    else:
        lines.append("### 시장 데이터: 수집 실패")

    lines += [
        "",
        "## Risk-On 조건 (Voting — 2개 이상 충족 시 Risk-On)",
        "- Fear & Greed > 60",
        "- ETF 순유입 > 0 (3일 평균)",
        "- Funding Rate 정상 범위 (0 ~ 0.01%)",
        "- 매크로 금리 동결/인하 기조",
        "",
        "## Risk-Off 조건 (1개라도 충족 시 즉시 Risk-Off)",
        "- Fear & Greed < 30",
        "- ETF 자금 순유출 3일 연속",
        "- Funding Rate 극단값 (> 0.1% 또는 < -0.05%)",
        "",
        "## 응답 형식 (JSON만 출력, 마크다운 코드블록 없이)",
        '{"risk": "risk-on" 또는 "risk-off", "summary": "한국어로 분석 요약 2~3문장"}',
    ]
    return "\n".join(lines)


def analyse(
    macro: MacroData | None,
    etf: ETFData | None,
    fear_greed: FearGreedData | None,
    market: MarketData | None,
) -> ExplorerReport:
    client = genai.Client()
    prompt = _build_prompt(macro, etf, fear_greed, market)
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=prompt,
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    parsed = json.loads(text.strip())
    return ExplorerReport(
        timestamp=datetime.now(UTC).isoformat(),
        risk=parsed["risk"],
        summary=parsed["summary"],
        macro=macro,
        etf=etf,
        fear_greed=fear_greed,
        market=market,
    )
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_explorer.py::test_analyse_returns_risk_on tests/test_explorer.py::test_analyse_returns_risk_off -v
```

Expected: 2 PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/explorer.py tests/test_explorer.py
git commit -m "feat: add Gemini analyse with ADC auth"
```

---

### Task 8: 파이프라인 (run_once + save_report)

**Files:**
- Modify: `src/explorer.py`
- Modify: `tests/test_explorer.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_explorer.py`에 추가:

```python
def test_run_once_writes_json(tmp_path, monkeypatch):
    from src import explorer
    from src.explorer import run_once

    monkeypatch.setattr(explorer, "REPORT_PATH", tmp_path / "explorer_report.json")

    fg = FearGreedData(score=70, label="Greed")
    market = MarketData(funding_rate=0.005, open_interest=1e9, long_short_ratio=1.2)
    report = ExplorerReport(
        timestamp="2026-05-06T00:00:00+00:00",
        risk="risk-on",
        summary="좋음",
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
        run_once()

    written = json.loads((tmp_path / "explorer_report.json").read_text(encoding="utf-8"))
    assert written["risk"] == "risk-on"
    assert written["timestamp"] == "2026-05-06T00:00:00+00:00"


def test_run_once_survives_collector_failure(tmp_path, monkeypatch):
    from src import explorer
    from src.explorer import run_once

    monkeypatch.setattr(explorer, "REPORT_PATH", tmp_path / "explorer_report.json")

    fg = FearGreedData(score=70, label="Greed")
    report = ExplorerReport(
        timestamp="2026-05-06T00:00:00+00:00",
        risk="risk-on",
        summary="ok",
        macro=None,
        etf=None,
        fear_greed=fg,
        market=None,
    )

    with patch("src.explorer.collect_macro", return_value=None), \
         patch("src.explorer.collect_etf", return_value=None), \
         patch("src.explorer.collect_fear_greed", return_value=fg), \
         patch("src.explorer.collect_market", return_value=None), \
         patch("src.explorer.analyse", return_value=report):
        run_once()  # 수집기 3개 None이어도 예외 없이 완료

    assert (tmp_path / "explorer_report.json").exists()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
uv run pytest tests/test_explorer.py::test_run_once_writes_json -v
```

Expected: `AttributeError` — run_once 미정의

- [ ] **Step 3: save_report + run_once 구현 (src/explorer.py에 추가)**

```python
def save_report(report: ExplorerReport) -> None:
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(asdict(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def run_once() -> None:
    try:
        macro = collect_macro()
        etf = collect_etf()
        fear_greed = collect_fear_greed()
        market = collect_market()

        if all(x is None for x in [macro, etf, fear_greed, market]):
            print("[explorer] all collectors failed — keeping previous risk state")
            return

        try:
            report = analyse(macro, etf, fear_greed, market)
        except Exception as e:
            print(f"[explorer] Gemini analysis failed: {e} — keeping previous risk state")
            return

        save_report(report)
        print(f"[explorer] {report.timestamp} risk={report.risk}")
    except Exception as e:
        print(f"[explorer] run_once unexpected error: {e}")
```

- [ ] **Step 4: 전체 테스트 통과 확인**

```bash
uv run pytest tests/test_explorer.py -v
```

Expected: 12 PASSED

- [ ] **Step 5: 커밋**

```bash
git add src/explorer.py tests/test_explorer.py
git commit -m "feat: add run_once pipeline and save_report"
```

---

### Task 9: 스케줄러 + 진입점

**Files:**
- Modify: `src/explorer.py`

- [ ] **Step 1: main() + __main__ 블록 추가 (src/explorer.py 맨 아래에 추가)**

```python
def main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    print("[explorer] starting — running once immediately")
    run_once()

    scheduler = BlockingScheduler()
    scheduler.add_job(run_once, "interval", hours=1)
    print("[explorer] scheduler started — runs every 1 hour (Ctrl+C to stop)")
    scheduler.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 전체 테스트 통과 확인**

```bash
uv run pytest tests/ -v
```

Expected: 19 PASSED (test_client 4 + test_account 3 + test_explorer 12)

- [ ] **Step 3: 스모크 테스트 (ADC 인증 필요)**

```bash
uv run python -m src.explorer
```

Expected 출력:
```
[explorer] starting — running once immediately
[explorer] 2026-05-06T...+00:00 risk=risk-on
[explorer] scheduler started — runs every 1 hour (Ctrl+C to stop)
```

`data/explorer_report.json` 파일 생성 확인:
```bash
uv run python -c "import json; print(json.load(open('data/explorer_report.json'))['risk'])"
```

Ctrl+C로 종료.

- [ ] **Step 4: 커밋**

```bash
git add src/explorer.py
git commit -m "feat: add APScheduler main() — explorer runs every 1 hour"
```

---

## Self-Review

**스펙 커버리지 체크:**
- ✅ 데이터 모델 (MacroData, ETFData, FearGreedData, MarketData, ExplorerReport) — Task 2
- ✅ collect_fear_greed (Alternative.me) — Task 3
- ✅ collect_market (Bitget 공개 API) — Task 4
- ✅ collect_macro (FRED API) — Task 5
- ✅ collect_etf (SoSoValue) — Task 6
- ✅ analyse (Gemini 2.5 Pro, ADC) — Task 7
- ✅ run_once 파이프라인 — Task 8
- ✅ save_report → data/explorer_report.json — Task 8
- ✅ APScheduler 1시간 간격 — Task 9
- ✅ 에러 처리 4가지 시나리오 — Task 8 run_once
- ✅ ADC 인증 (api_key 없음) — Task 7

**타입 일관성:**
- `FearGreedData`, `MarketData`, `MacroData`, `ETFData`, `ExplorerReport` — 모든 Task에서 동일
- `collect_*() -> X | None` 시그니처 — 테스트와 구현 일치
- `REPORT_PATH: Path` — Task 8 monkeypatch 대상과 일치
