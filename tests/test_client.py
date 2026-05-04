import os
import pytest
from unittest.mock import patch, MagicMock
from importlib import reload


def test_raises_when_api_key_missing():
    env = {"BITGET_API_KEY": "", "BITGET_SECRET_KEY": "s", "BITGET_PASSPHRASE": "p"}
    with patch.dict(os.environ, env, clear=False):
        import src.client as m; reload(m)
        with pytest.raises(ValueError) as exc:
            m.get_client()
        assert "BITGET_API_KEY" in str(exc.value)


def test_raises_when_multiple_vars_missing():
    env = {"BITGET_API_KEY": "", "BITGET_SECRET_KEY": "", "BITGET_PASSPHRASE": ""}
    with patch.dict(os.environ, env, clear=False):
        import src.client as m; reload(m)
        with pytest.raises(ValueError) as exc:
            m.get_client()
        assert "BITGET_API_KEY" in str(exc.value)
        assert "BITGET_SECRET_KEY" in str(exc.value)


def test_injects_demo_header_when_is_demo_true():
    env = {
        "BITGET_API_KEY": "k", "BITGET_SECRET_KEY": "s",
        "BITGET_PASSPHRASE": "p", "BITGET_IS_DEMO": "True",
    }
    with patch.dict(os.environ, env, clear=False):
        mock_client = MagicMock()
        mock_client.account.request_handler.static_headers = {}
        with patch("src.client.BitgetAPI", return_value=mock_client):
            import src.client as m; reload(m)
            result = m.get_client()
        assert result.account.request_handler.static_headers.get("x-simulated-trading") == "1"


def test_no_demo_header_when_is_demo_false():
    env = {
        "BITGET_API_KEY": "k", "BITGET_SECRET_KEY": "s",
        "BITGET_PASSPHRASE": "p", "BITGET_IS_DEMO": "false",
    }
    with patch.dict(os.environ, env, clear=False):
        mock_client = MagicMock()
        mock_client.account.request_handler.static_headers = {}
        with patch("src.client.BitgetAPI", return_value=mock_client):
            import src.client as m; reload(m)
            result = m.get_client()
        assert "x-simulated-trading" not in result.account.request_handler.static_headers
