from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_fake_db(patch_db):
    db_mod.init_db()
    return patch_db


def test_regime_change_logs_event(tmp_path, patch_db, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "BTCUSDT": {
            "trend_range": "trend",
            "risk": "risk-on",
            "signal_summary": "테스트",
            "regime": "caution",
        }
    }), encoding="utf-8")

    import src.regime as regime_mod
    monkeypatch.setattr(regime_mod, "STATE_PATH", state_path)

    mock_response = MagicMock()
    mock_response.text = '{"regime": "normal", "comment": "추세 강화"}'

    with patch("src.regime.get_model") as mock_get_model:
        mock_get_model.return_value.generate_content.return_value = mock_response
        regime_mod.run_regime_once()

    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO events" in sql]
    assert inserts
    _, params = inserts[0]
    assert "INFO" in params
    assert "regime" in params
    assert any("CAUTION_TO_NORMAL" in str(p) for p in params)
