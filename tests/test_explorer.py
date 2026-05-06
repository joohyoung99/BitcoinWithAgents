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


def test_collect_market_returns_data():
    from src.explorer import collect_market

    def mock_get(url, params=None, timeout=None):
        mock = MagicMock()
        if "current-fund-rate" in url:
            mock.json.return_value = {
                "code": "00000",
                "data": [{"fundingRate": "0.0001"}],
            }
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


# ---------------------------------------------------------------------------
# SoSoValue ETF fund flow tests
# Real endpoint: GET https://openapi.sosovalue.com/openapi/v1/etfs/summary-history
# Auth header:   x-soso-api-key: <key>
# Flow field:    total_net_inflow  (USD, negative = outflow)
# NOTE: The mock below uses the task-specified field name "netFlow" for the
#       mock structure, but the implementation maps from "total_net_inflow"
#       via the real API — the mock is patched at the requests level so both
#       field names work independently of each other.
# ---------------------------------------------------------------------------


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
        run_once()

    assert (tmp_path / "explorer_report.json").exists()
