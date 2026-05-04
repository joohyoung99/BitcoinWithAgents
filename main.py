import sys
from src.client import get_client
from src.account import get_balance


def main():
    try:
        client = get_client()
        balance = get_balance(client)
    except ValueError as e:
        print(f"[설정 오류] {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"[API 오류] {e}", file=sys.stderr)
        sys.exit(1)

    print("=== Bitget Demo UTA 잔고 ===")
    print(f"  총 자산       : {balance['total']:,.2f} USDT")
    print(f"  가용 증거금   : {balance['available']:,.2f} USDT")
    print(f"  미실현 손익   : {balance['unrealized_pnl']:,.2f} USDT")


if __name__ == "__main__":
    main()
