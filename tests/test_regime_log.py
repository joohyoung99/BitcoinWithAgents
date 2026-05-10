from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db()


def test_regime_change_logs_event(tmp_path, monkeypatch):
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

    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT level, module, message FROM events").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "INFO"
    assert row[1] == "regime"
    assert "CAUTION_TO_NORMAL" in row[2]
