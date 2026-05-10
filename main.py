from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

from src.account import get_balance
from src.client import get_client


def _setup_stream_handler() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(fmt)
        root.addHandler(handler)


def _healthcheck(timeout: int = 10) -> dict:
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(lambda: get_balance(get_client()))
        return future.result(timeout=timeout)


def main() -> None:
    _setup_stream_handler()

    try:
        balance = _healthcheck(timeout=10)
    except FuturesTimeout:
        logging.error("헬스체크 10초 초과 — Bitget API 응답 없음")
        sys.exit(1)
    except ValueError as e:
        logging.error(f"환경변수 오류: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"헬스체크 실패: {e}")
        sys.exit(1)

    logging.info(
        f"헬스체크 통과: 잔고={balance['total']:,.2f} USDT, "
        f"가용={balance['available']:,.2f} USDT, "
        f"미실현PnL={balance['unrealized_pnl']:,.2f} USDT"
    )

    try:
        from src.explorer import main as run_system  # lazy: 헬스체크 통과 후 임포트
        run_system()
    except KeyboardInterrupt:
        logging.info("시스템 종료 (Ctrl+C)")


if __name__ == "__main__":
    main()
