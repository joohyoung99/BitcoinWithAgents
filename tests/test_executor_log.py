from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_fake_db(patch_db):
    db_mod.init_db()
    return patch_db


def _make_client(json_return=None, side_effect=None):
    mock_client = MagicMock()
    rh = mock_client.account.request_handler
    rh.base_url = "https://api.bitget.com"
    rh._get_headers.return_value = {}
    if side_effect:
        rh.session.post.side_effect = side_effect
    else:
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_return
        rh.session.post.return_value = mock_resp
    return mock_client


def test_bitget_post_logs_api_on_success(patch_db):
    client = _make_client(json_return={"code": "00000", "data": {}})
    from src.executor import _bitget_post
    result = _bitget_post(client, "/api/v2/mix/order/place-order", {"symbol": "BTCUSDT"})

    assert result == {"code": "00000", "data": {}}
    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO api_logs" in sql]
    assert inserts
    _, params = inserts[0]
    assert "/api/v2/mix/order/place-order" in params
    assert "00000" in params


def test_bitget_post_logs_api_on_failure(patch_db):
    client = _make_client(side_effect=ConnectionError("timeout"))
    from src.executor import _bitget_post
    result = _bitget_post(client, "/api/v2/mix/order/place-order", {"symbol": "BTCUSDT"})

    assert result is None
    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO api_logs" in sql]
    assert inserts
    _, params = inserts[0]
    assert "ERR" in params
