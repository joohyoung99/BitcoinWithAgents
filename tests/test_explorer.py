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
