import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from src.models import ChartSignal


def test_chart_signal_dataclass():
    sig = ChartSignal(
        symbol="BTCUSDT",
        updated_at="2026-05-06T00:00:00+00:00",
        trend_range="trend",
        adx=28.5,
        rsi=52.3,
        ema_aligned=True,
        ema50_slope=0.0023,
        atr=1250.5,
        entry_signal="long",
        confidence=78,
        signal_summary="test",
    )
    assert sig.symbol == "BTCUSDT"
    assert sig.entry_signal == "long"
    assert sig.confidence == 78


def test_fetch_candles_returns_dataframe():
    from src.chart import fetch_candles

    rows = [
        [str(1000 + i), "50000", "50100", "49900", "50050", "10", "500000"]
        for i in range(10, 0, -1)
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": rows}

    with patch("src.chart.requests.get", return_value=mock_resp):
        df = fetch_candles()

    assert len(df) == 9
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df["timestamp"].iloc[0] < df["timestamp"].iloc[-1]
    assert df["close"].dtype == float
