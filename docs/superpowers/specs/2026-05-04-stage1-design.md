# Stage 1 Design — Bitget Demo API 연결 및 잔고 조회

**날짜:** 2026-05-04  
**범위:** 1단계 (API 연결 확인 + USDT-FUTURES 잔고 조회)  
**완료 기준:** Demo UTA에 연결하여 잔고를 출력하는 것까지

---

## 선택한 접근법

**Bitget 공식 Python SDK (`bitget-python`)** 사용.

이유: Demo UTA의 `X-SIMULATED-TRADING: 1` 헤더를 SDK 레벨에서 관리하고, 이후 단계(주문 실행, 포지션 조회)에서도 동일한 클라이언트를 재사용하기 위함.

---

## 파일 구조

```
BitcoinWithAgents/
├── src/
│   ├── __init__.py
│   ├── client.py      # BitgetClient 초기화 — Demo 헤더 주입
│   └── account.py     # get_balance() 함수
├── main.py            # 진입점 — 잔고 출력
├── .env               # 크리덴셜 (gitignore에 포함)
└── pyproject.toml
```

---

## 모듈 설계

### `src/client.py`

- `python-dotenv`로 `.env`에서 `BITGET_API_KEY`, `BITGET_SECRET_KEY`, `BITGET_PASSPHRASE` 로드
- Bitget SDK 클라이언트 초기화 시 `X-SIMULATED-TRADING: 1` 헤더 포함
- 싱글턴 패턴 없이 함수(`get_client()`)로 제공 — 호출마다 생성 (Stage 1 수준에서 충분)

### `src/account.py`

- `get_balance(client) -> dict` 함수 하나
- 조회 대상: USDT-FUTURES 계좌 (`productType=USDT-FUTURES`)
- 반환값: `{ "total": float, "available": float, "unrealized_pnl": float }`
- 에러 처리: 인증 실패 / 네트워크 오류 시 예외를 그대로 올려보냄 (main.py에서 캐치)

### `main.py`

- `get_client()` 호출 → `get_balance(client)` 호출 → 결과 포맷 출력
- 예외 발생 시 원인 메시지 출력 후 종료 (exit code 1)

---

## 환경변수 (.env)

```
BITGET_API_KEY=
BITGET_SECRET_KEY=
BITGET_PASSPHRASE=
```

`.gitignore`에 `.env` 추가 필요.

---

## 설치 패키지

```
bitget-python
python-dotenv
```

---

## 이 설계가 이후 단계에 미치는 영향

- `src/client.py`의 `get_client()`는 2단계(탐색 에이전트)부터 공유 사용
- `src/` 디렉터리에 이후 `explorer.py`, `chart.py`, `regime.py`, `order.py` 등이 추가될 예정
- Demo 헤더(`X-SIMULATED-TRADING: 1`)는 실거래 전환 시 `client.py` 한 곳만 수정하면 됨
