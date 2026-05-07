from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db()


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


def test_bitget_post_logs_api_on_success():
    client = _make_client(json_return={"code": "00000", "data": {}})
    from src.executor import _bitget_post
    result = _bitget_post(client, "/api/v2/mix/order/place-order", {"symbol": "BTCUSDT"})

    assert result == {"code": "00000", "data": {}}
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT endpoint, status_code FROM api_logs").fetchone()
    conn.close()
    assert row == ("/api/v2/mix/order/place-order", "00000")


def test_bitget_post_logs_api_on_failure():
    client = _make_client(side_effect=ConnectionError("timeout"))
    from src.executor import _bitget_post
    result = _bitget_post(client, "/api/v2/mix/order/place-order", {"symbol": "BTCUSDT"})

    assert result is None
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT endpoint, status_code FROM api_logs").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "ERR"
