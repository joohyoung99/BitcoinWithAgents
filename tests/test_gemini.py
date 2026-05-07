from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db()


def test_generate_content_logs_llm_on_success():
    mock_result = MagicMock()
    mock_result.text = "test response"

    with patch("src.gemini._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_result
        mock_get_client.return_value = mock_client

        from src.gemini import get_model
        result = get_model("gemini-2.5-flash").generate_content("test prompt")

    assert result.text == "test response"

    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT module, model, success FROM llm_logs").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "gemini-2.5-flash"
    assert row[2] == 1


def test_generate_content_logs_llm_on_failure():
    with patch("src.gemini._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API error")
        mock_get_client.return_value = mock_client

        from src.gemini import get_model
        with pytest.raises(RuntimeError):
            get_model("gemini-2.5-flash").generate_content("fail prompt")

    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT success FROM llm_logs").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0
