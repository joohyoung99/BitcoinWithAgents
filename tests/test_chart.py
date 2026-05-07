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


def _make_df_with_adx(adx: float, slope: float = 0.001) -> pd.DataFrame:
    return pd.DataFrame({
        "ADX_14": [adx],
        "RSI_14": [50.0],
        "EMA_20": [100.0],
        "EMA_50": [95.0],
        "EMA_200": [90.0],
        "ema50_slope": [slope],
        "close": [97.0],
        "BBL_20_2.0": [88.0],
        "BBU_20_2.0": [112.0],
        "ATRr_14": [2.0],
    })


def test_determine_trend_range_trend():
    from src.chart import determine_trend_range
    df = _make_df_with_adx(adx=30.0, slope=0.002)
    assert determine_trend_range(df, prev_trend_range="range") == "trend"


def test_determine_trend_range_hysteresis_keeps_trend():
    from src.chart import determine_trend_range
    df = _make_df_with_adx(adx=23.0, slope=0.001)
    assert determine_trend_range(df, prev_trend_range="trend") == "trend"


def test_determine_trend_range_hysteresis_keeps_range():
    from src.chart import determine_trend_range
    df = _make_df_with_adx(adx=21.0, slope=-0.001)
    assert determine_trend_range(df, prev_trend_range="range") == "range"


def test_determine_trend_range_range():
    from src.chart import determine_trend_range
    df = _make_df_with_adx(adx=15.0)
    assert determine_trend_range(df, prev_trend_range="trend") == "range"


def test_determine_entry_normal_long():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=30.0)
    # close=97, EMA20=100, EMA50=95 → EMA50 ≤ close ≤ EMA20, RSI=50 → long
    assert determine_entry(df, "trend", "risk-on") == "long"


def test_determine_entry_blocks_long_on_risk_off():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=30.0)
    assert determine_entry(df, "trend", "risk-off") != "long"


def test_determine_entry_halt():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=15.0)
    assert determine_entry(df, "range", "risk-off") == "none"


def test_determine_entry_caution_bb_short():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=15.0)
    df = df.copy()
    df["close"] = 112.0
    assert determine_entry(df, "range", "risk-on") == "short"


def test_determine_entry_caution_bb_long():
    from src.chart import determine_entry
    df = _make_df_with_adx(adx=15.0)
    df = df.copy()
    df["close"] = 88.0
    assert determine_entry(df, "range", "risk-on") == "long"


def test_score_signal_returns_confidence_and_comment():
    from src.chart import score_signal

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"confidence": 75, "comment": "Strong uptrend"}'
    mock_client.models.generate_content.return_value = mock_response

    with patch("src.chart.genai.Client", return_value=mock_client):
        confidence, comment = score_signal(_make_ohlcv(300), "long", "trend")

    assert confidence == 75
    assert "uptrend" in comment.lower()


def test_score_signal_fallback_on_gemini_fail():
    from src.chart import score_signal

    with patch("src.chart.genai.Client", side_effect=Exception("API error")):
        confidence, comment = score_signal(_make_ohlcv(300), "long", "trend")

    assert confidence == 50
    assert comment == "LLM unavailable"


def test_update_state_writes_atomic_with_meta(tmp_path, monkeypatch):
    from src.chart import update_state
    from src.models import ChartSignal
    import src.chart as chart_mod

    monkeypatch.setattr(chart_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(chart_mod, "REPORT_PATH", tmp_path / "explorer_report.json")

    # write a fake explorer report
    (tmp_path / "explorer_report.json").write_text(
        json.dumps({"risk": "risk-on", "summary": "test summary"}),
        encoding="utf-8",
    )

    sig = ChartSignal(
        symbol="BTCUSDT",
        updated_at="2026-05-06T12:00:00+00:00",
        trend_range="trend",
        adx=28.5,
        rsi=52.3,
        ema_aligned=True,
        ema50_slope=0.002,
        atr=1200.0,
        entry_signal="long",
        confidence=75,
        signal_summary="EMA 정배열",
    )
    update_state(sig)

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "meta" in state
    assert state["meta"]["schema_version"] == 1
    assert "updated_at" in state["meta"]
    assert "BTCUSDT" in state
    assert state["BTCUSDT"]["entry_signal"] == "long"
    assert state["BTCUSDT"]["risk"] == "risk-on"
    assert state["BTCUSDT"]["risk_summary"] == "test summary"
