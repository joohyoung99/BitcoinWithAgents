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
