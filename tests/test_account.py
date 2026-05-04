import pytest
from unittest.mock import MagicMock
from src.account import get_balance


def _make_client(code: str, data: list) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.code = code
    response.data = data
    client.account.get_accounts.return_value = response
    return client


def test_returns_parsed_balance():
    account = MagicMock()
    account.equity = "50000.00"
    account.available = "49500.00"
    account.unrealizedPL = "500.00"

    client = _make_client("00000", [account])
    result = get_balance(client)

    client.account.get_accounts.assert_called_once_with("USDT-FUTURES")
    assert result == {
        "total": 50000.0,
        "available": 49500.0,
        "unrealized_pnl": 500.0,
    }


def test_raises_on_api_error_code():
    client = _make_client("40001", [])
    client.account.get_accounts.return_value.msg = "Invalid apikey"

    with pytest.raises(RuntimeError) as exc:
        get_balance(client)
    assert "40001" in str(exc.value)


def test_raises_when_data_is_empty():
    client = _make_client("00000", [])

    with pytest.raises(RuntimeError) as exc:
        get_balance(client)
    assert "empty" in str(exc.value).lower()
