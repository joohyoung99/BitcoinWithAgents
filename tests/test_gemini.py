from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_generate_content_returns_result():
    mock_result = MagicMock()
    mock_result.text = "test response"

    with patch("src.gemini._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_result
        mock_get_client.return_value = mock_client

        from src.gemini import get_model
        result = get_model("gemini-2.5-flash").generate_content("test prompt")

    assert result.text == "test response"


def test_generate_content_raises_on_failure():
    with patch("src.gemini._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API error")
        mock_get_client.return_value = mock_client

        from src.gemini import get_model
        with pytest.raises(RuntimeError):
            get_model("gemini-2.5-flash").generate_content("fail prompt")
