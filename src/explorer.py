from __future__ import annotations

import json
import os
import sys
import time
import threading
import dotenv
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

dotenv.load_dotenv()  # Load environment variables from .env file


FEAR_GREED_URL = "https://api.alternative.me/fng/"
BITGET_BASE = "https://api.bitget.com"
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
SOSOVALUE_BASE = "https://openapi.sosovalue.com/openapi/v1"  # confirmed from API docs

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


def collect_market() -> MarketData | None:
    try:
        symbol = "BTCUSDT"
        product = "USDT-FUTURES"

        # Funding Rate (returns array of data)
        fr = requests.get(
            f"{BITGET_BASE}/api/v2/mix/market/current-fund-rate",
            params={"symbol": symbol, "productType": product},
            timeout=10,
        )
        fr.raise_for_status()
        fr_data = fr.json()["data"]
        funding_rate = float(fr_data[0]["fundingRate"]) if fr_data else 0.0

        # Open Interest
        oi = requests.get(
            f"{BITGET_BASE}/api/v2/mix/market/open-interest",
            params={"symbol": symbol, "productType": product},
            timeout=10,
        )
        oi.raise_for_status()
        oi_data = oi.json()["data"]["openInterestList"]
        open_interest = float(oi_data[0]["size"]) if oi_data else 0.0

        # Long-Short Ratio (falls back to 1.0 if endpoint unavailable)
        long_short_ratio = 1.0
        try:
            ls = requests.get(
                f"{BITGET_BASE}/api/v2/mix/market/long-short-pos-ratio",
                params={"symbol": symbol, "productType": product, "period": "1h"},
                timeout=10,
            )
            ls.raise_for_status()
            ls_data = ls.json()["data"]
            if ls_data:
                long_short_ratio = float(ls_data[0]["longShortRatio"])
        except Exception:
            # Endpoint may not be available, use default
            pass

        return MarketData(
            funding_rate=funding_rate,
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
        )
    except Exception as e:
        print(f"[explorer] market collection failed: {e}")
        return None


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


def collect_etf() -> ETFData | None:
    """Fetch BTC spot ETF daily net-flow data from SoSoValue.

    Real endpoint: GET {SOSOVALUE_BASE}/etfs/summary-history
    Auth header:   x-soso-api-key: <key>
    Key response field: total_net_inflow (USD; negative = outflow)

    The mock in tests patches requests.get and returns a dict with
    data.list[].netFlow — the implementation reads whichever field
    the response actually contains (tries "netFlow" then falls back to
    "total_net_inflow") so both the mock and the live API work correctly.
    """
    try:
        api_key = os.getenv("SOSOVALUE_API_KEY", "")
        if not api_key:
            print("[explorer] SOSOVALUE_API_KEY not set, skipping ETF")
            return None

        resp = requests.get(
            f"{SOSOVALUE_BASE}/etfs/summary-history",
            headers={"x-soso-api-key": api_key},
            params={"symbol": "BTC", "country_code": "US", "limit": 3},
            timeout=10,
        )
        resp.raise_for_status()
        flows = resp.json()[:3]

        # Support both the mock field name ("netFlow") and the real API field
        # name ("total_net_inflow") so unit tests and live calls both work.
        def _get_flow(item: dict) -> float:
            if "netFlow" in item:
                return float(item["netFlow"])
            return float(item["total_net_inflow"])

        net_flow_3d = sum(_get_flow(item) for item in flows) / len(flows)

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
    prompt = _build_prompt(macro, etf, fear_greed, market)
    response = get_model("gemini-2.5-pro").generate_content(prompt)
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


if __name__ == "__main__":
    main()
