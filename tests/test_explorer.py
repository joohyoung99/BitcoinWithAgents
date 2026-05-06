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
