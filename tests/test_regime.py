import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from src.models import RegimeState


def test_regime_state_dataclass():
    rs = RegimeState(
        symbol="BTCUSDT",
        regime="normal",
        prev_regime="caution",
        regime_changed=True,
        regime_transition="CAUTION_TO_NORMAL",
        regime_summary="EMA 정배열 확인됨",
        regime_updated_at="2026-05-07T00:00:00+00:00",
    )
    assert rs.regime == "normal"
    assert rs.regime_changed is True
    assert rs.regime_transition == "CAUTION_TO_NORMAL"


def test_classify_normal():
    from src.regime import classify_regime
    assert classify_regime("trend", "risk-on") == "normal"


def test_classify_caution():
    from src.regime import classify_regime
    assert classify_regime("range", "risk-on") == "caution"


def test_classify_risk_off_trend():
    from src.regime import classify_regime
    assert classify_regime("trend", "risk-off") == "risk_off_trend"


def test_classify_halt():
    from src.regime import classify_regime
    assert classify_regime("range", "risk-off") == "halt"


def test_classify_unknown_defaults_halt():
    from src.regime import classify_regime
    assert classify_regime("unknown", "garbage") == "halt"
    assert classify_regime("trend", "") == "halt"
    assert classify_regime("", "risk-on") == "halt"
