import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from src.models import RegimeState
import src.regime as regime_mod


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


def test_review_regime_gemini_agrees():
    from src.regime import review_regime_with_gemini

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"regime": "normal", "comment": "EMA 정배열 확인"}'
    mock_client.generate_content.return_value = mock_resp

    with patch("src.regime.get_model", return_value=mock_client):
        result = review_regime_with_gemini("normal", "trend", "risk-on", "EMA 상승중")

    assert result == "normal"


def test_review_regime_gemini_fails_returns_rule_regime():
    from src.regime import review_regime_with_gemini

    with patch("src.regime.get_model", side_effect=Exception("API error")):
        result = review_regime_with_gemini("caution", "range", "risk-on", "횡보")

    assert result == "caution"


def test_run_regime_once_writes_state(tmp_path, monkeypatch):
    import src.regime as regime_mod
    from src.regime import run_regime_once

    monkeypatch.setattr(regime_mod, "STATE_PATH", tmp_path / "state.json")

    now_iso = datetime.now(UTC).isoformat()
    state = {
        "meta": {"schema_version": 1, "updated_at": now_iso},
        "BTCUSDT": {
            "updated_at": now_iso,
            "risk": "risk-on",
            "trend_range": "trend",
            "signal_summary": "EMA 상승",
        },
    }
    (tmp_path / "state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"regime": "normal", "comment": "정배열 확인"}'
    mock_client.generate_content.return_value = mock_resp

    with patch("src.regime.get_model", return_value=mock_client):
        run_regime_once()

    result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert result["BTCUSDT"]["regime"] == "normal"
    assert "regime_changed" in result["BTCUSDT"]
    assert "regime_updated_at" in result["BTCUSDT"]


def test_run_regime_once_detects_change(tmp_path, monkeypatch):
    import src.regime as regime_mod
    from src.regime import run_regime_once

    monkeypatch.setattr(regime_mod, "STATE_PATH", tmp_path / "state.json")

    now_iso = datetime.now(UTC).isoformat()
    state = {
        "meta": {"schema_version": 1, "updated_at": now_iso},
        "BTCUSDT": {
            "updated_at": now_iso,
            "risk": "risk-on",
            "trend_range": "trend",
            "signal_summary": "추세 진입",
            "regime": "caution",
        },
    }
    (tmp_path / "state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"regime": "normal", "comment": "정배열"}'
    mock_client.generate_content.return_value = mock_resp

    with patch("src.regime.get_model", return_value=mock_client):
        run_regime_once()

    result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert result["BTCUSDT"]["regime"] == "normal"
    assert result["BTCUSDT"]["regime_changed"] is True
    assert result["BTCUSDT"]["regime_transition"] == "CAUTION_TO_NORMAL"
    assert result["BTCUSDT"]["prev_regime"] == "caution"
