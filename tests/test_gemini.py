from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_fake_db(patch_db):
    db_mod.init_db()
    return patch_db


def test_generate_content_logs_llm_on_success(patch_db):
    mock_result = MagicMock()
    mock_result.text = "test response"

    with patch("src.gemini._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_result
        mock_get_client.return_value = mock_client

        from src.gemini import get_model
        result = get_model("gemini-2.5-flash").generate_content("test prompt")

    assert result.text == "test response"

    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO llm_logs" in sql]
    assert inserts
    _, params = inserts[0]
    assert "gemini-2.5-flash" in params
    assert 1 in params  # success=True → 1
    assert any(p != "" for p in params if isinstance(p, str))  # module non-empty


def test_generate_content_logs_llm_on_failure(patch_db):
    with patch("src.gemini._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API error")
        mock_get_client.return_value = mock_client

        from src.gemini import get_model
        with pytest.raises(RuntimeError):
            get_model("gemini-2.5-flash").generate_content("fail prompt")

    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO llm_logs" in sql]
    assert inserts
    _, params = inserts[0]
    assert 0 in params  # success=False → 0
