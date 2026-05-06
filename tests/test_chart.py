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


def _make_ohlcv(n: int = 300) -> pd.DataFrame:
    import numpy as np
    rng = np.random.default_rng(42)
    close = 50000 + rng.normal(0, 200, n).cumsum()
    df = pd.DataFrame({
        "timestamp": range(n),
        "open": close - 50,
        "high": close + 100,
        "low": close - 100,
        "close": close,
        "volume": rng.uniform(100, 500, n),
    })
    return df


def test_calc_indicators_adds_columns():
    from src.chart import calc_indicators

    df = calc_indicators(_make_ohlcv(300))

    for col in ["EMA_20", "EMA_50", "EMA_200", "ADX_14", "RSI_14", "ATRr_14", "OBV"]:
        assert col in df.columns, f"missing column: {col}"
    assert "ema50_slope" in df.columns
    assert len(df) > 0
    assert not df["EMA_20"].isna().any()


def test_calc_indicators_ema50_slope_uses_iloc():
    from src.chart import calc_indicators

    df = calc_indicators(_make_ohlcv(300))
    # slope = (ema50[-1] - ema50[-4]) / ema50[-4]
    ema50 = df["EMA_50"]
    expected = (ema50.iloc[-1] - ema50.iloc[-4]) / ema50.iloc[-4]
    assert abs(df["ema50_slope"].iloc[-1] - expected) < 1e-10
