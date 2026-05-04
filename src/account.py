from bitpy.rest_api import BitgetAPI


def get_balance(client: BitgetAPI) -> dict:
    response = client.account.get_accounts("USDT-FUTURES")

    if response.code != "00000":
        raise RuntimeError(f"Bitget API error {response.code}: {response.msg}")

    if not response.data:
        raise RuntimeError("Bitget API returned empty data for account balance")

    account = response.data[0]  # USDT-FUTURES는 단일 계좌 반환
    return {
        "total": float(account.accountEquity),
        "available": float(account.available),
        "unrealized_pnl": float(account.unrealizedPL),
    }
