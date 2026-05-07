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
