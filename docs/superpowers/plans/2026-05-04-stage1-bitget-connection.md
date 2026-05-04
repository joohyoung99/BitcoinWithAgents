# Stage 1: Bitget Demo API 연결 및 잔고 조회 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bitget Demo UTA에 연결하여 USDT-FUTURES 계좌 잔고를 출력하는 최소 동작 구현

**Architecture:** `src/client.py`가 SDK 초기화 + Demo 헤더 주입을 담당하고, `src/account.py`가 잔고 조회 로직을 담당한다. `main.py`는 두 모듈을 연결해 결과를 출력하는 진입점이다.

**Tech Stack:** Python 3.11+, `bitget` (Bitget 공식 SDK), `python-dotenv`, `pytest`

---

## File Map

| 작업 | 파일 |
|------|------|
| Create | `src/__init__.py` |
| Create | `src/client.py` |
| Create | `src/account.py` |
| Create | `tests/__init__.py` |
| Create | `tests/test_client.py` |
| Create | `tests/test_account.py` |
| Create | `.env.example` |
| Modify | `main.py` |
| Modify | `.gitignore` |
| Modify | `pyproject.toml` |

---

## Task 1: 프로젝트 기반 설정

**Files:**
- Modify: `.gitignore`
- Modify: `pyproject.toml`
- Create: `.env.example`
- Create: `src/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: `.gitignore`에 `.env` 추가**

`.gitignore` 파일을 열어 아래 두 줄을 추가한다:

```
# Environment secrets
.env
```

- [ ] **Step 2: `.env.example` 생성**

```
BITGET_API_KEY=
BITGET_SECRET_KEY=
BITGET_PASSPHRASE=
```

- [ ] **Step 3: 패키지 설치**

```bash
uv add bitget python-dotenv pytest
```

기대 출력: `pyproject.toml`의 `dependencies`에 세 패키지가 추가됨.

- [ ] **Step 4: `pyproject.toml`에 pytest 설정 추가**

`[project]` 섹션 아래에 추가:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 5: 디렉터리 및 `__init__.py` 생성**

```bash
uv run python -c "import pathlib; [pathlib.Path(p).mkdir(parents=True, exist_ok=True) or open(p+'/__init__.py','w').close() for p in ['src','tests']]"
```

- [ ] **Step 6: 커밋**

```bash
git add .gitignore .env.example pyproject.toml src/__init__.py tests/__init__.py
git commit -m "chore: project scaffold for stage 1"
```

---

## Task 2: `src/client.py` 구현 (TDD)

**Files:**
- Create: `tests/test_client.py`
- Create: `src/client.py`

`bitpy.rest_api.BitgetAPI`를 초기화하고 `BITGET_IS_DEMO=True`일 때 Demo 헤더(`x-simulated-trading: 1`)를 주입하는 `get_client()` 함수를 TDD로 구현한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_client.py`:

```python
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
    mock_client = MagicMock()
    mock_client.account.request_handler.static_headers = {}
    import src.client as m
    with patch.dict(os.environ, env, clear=False), \
         patch("src.client.BitgetAPI", return_value=mock_client):
        result = m.get_client()
    assert result is mock_client
    assert result.account.request_handler.static_headers.get("x-simulated-trading") == "1"


def test_no_demo_header_when_is_demo_false():
    env = {
        "BITGET_API_KEY": "k", "BITGET_SECRET_KEY": "s",
        "BITGET_PASSPHRASE": "p", "BITGET_IS_DEMO": "false",
    }
    mock_client = MagicMock()
    mock_client.account.request_handler.static_headers = {}
    import src.client as m
    with patch.dict(os.environ, env, clear=False), \
         patch("src.client.BitgetAPI", return_value=mock_client):
        result = m.get_client()
    assert result is mock_client
    assert "x-simulated-trading" not in result.account.request_handler.static_headers
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/test_client.py -v
```

기대: `ImportError` (파일 없으므로)

- [ ] **Step 3: `src/client.py` 구현**

```python
import os
from dotenv import load_dotenv
from bitpy.rest_api import BitgetAPI

load_dotenv()


def get_client() -> BitgetAPI:
    api_key = os.getenv("BITGET_API_KEY", "")
    api_secret = os.getenv("BITGET_SECRET_KEY", "")
    passphrase = os.getenv("BITGET_PASSPHRASE", "")

    missing = [
        name
        for name, val in [
            ("BITGET_API_KEY", api_key),
            ("BITGET_SECRET_KEY", api_secret),
            ("BITGET_PASSPHRASE", passphrase),
        ]
        if not val
    ]
    if missing:
        raise ValueError(f"Missing env vars: {', '.join(missing)}")

    client = BitgetAPI(api_key=api_key, secret_key=api_secret, api_passphrase=passphrase)

    if os.getenv("BITGET_IS_DEMO", "false").lower() == "true":
        client.account.request_handler.static_headers["x-simulated-trading"] = "1"

    return client
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/test_client.py -v
```

4개 모두 PASSED여야 한다.

- [ ] **Step 5: 커밋**

```bash
git add src/client.py tests/test_client.py
git commit -m "feat: add get_client with bitpy SDK and BITGET_IS_DEMO demo header injection"
```

---

## Task 3: `src/account.py` 구현 (TDD)

**Files:**
- Create: `tests/test_account.py`
- Create: `src/account.py`

Bitget API 응답에서 총 자산, 가용 증거금, 미실현 손익을 파싱하는 `get_balance()` 함수를 구현한다.

Bitget v2 API `GET /api/v2/mix/account/accounts` 응답 형식:
```json
{
  "code": "00000",
  "data": [
    {
      "equity": "50000.00",
      "available": "49500.00",
      "unrealizedPL": "500.00"
    }
  ]
}
```

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_account.py`:

```python
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
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/test_account.py -v
```

기대 출력: `ImportError` (파일이 없으므로)

- [ ] **Step 3: `src/account.py` 구현**

```python
from bitpy.rest_api import BitgetAPI


def get_balance(client: BitgetAPI) -> dict:
    response = client.account.get_accounts("USDT-FUTURES")

    if response.code != "00000":
        raise RuntimeError(f"Bitget API error {response.code}: {response.msg}")

    if not response.data:
        raise RuntimeError("Bitget API returned empty data for account balance")

    account = response.data[0]
    return {
        "total": float(account.equity),
        "available": float(account.available),
        "unrealized_pnl": float(account.unrealizedPL),
    }
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/test_account.py -v
```

기대 출력:
```
PASSED tests/test_account.py::test_returns_parsed_balance
PASSED tests/test_account.py::test_raises_on_api_error_code
PASSED tests/test_account.py::test_raises_when_data_is_empty
```

- [ ] **Step 5: 커밋**

```bash
git add src/account.py tests/test_account.py
git commit -m "feat: add get_balance for USDT-FUTURES account"
```

---

## Task 4: `main.py` 업데이트

**Files:**
- Modify: `main.py`

- [ ] **Step 1: `main.py` 작성**

```python
import sys
from src.client import get_account_api
from src.account import get_balance


def main():
    try:
        service = get_account_api()
        balance = get_balance(service)
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
```

- [ ] **Step 2: 전체 테스트 실행**

```bash
uv run pytest -v
```

기대 출력: 6개 테스트 모두 PASSED

- [ ] **Step 3: 커밋**

```bash
git add main.py
git commit -m "feat: wire up main.py for balance display"
```

---

## Task 5: Demo API 연결 검증 (실제 키 필요)

**Files:**
- Create: `.env` (로컬에만 존재, git에 포함 안 됨)

- [ ] **Step 1: `.env` 파일 생성**

Bitget 거래소에서 Demo 계좌의 API Key / Secret / Passphrase를 발급받아 `.env`에 입력:

```
BITGET_API_KEY=발급받은_키
BITGET_SECRET_KEY=발급받은_시크릿
BITGET_PASSPHRASE=발급받은_패스프레이즈
```

> Bitget 발급 경로: Demo 거래 → 설정 → API 관리 → System Generated API Key 생성  
> 권한: Read + Futures Trading (Withdraw 제외)

- [ ] **Step 2: 실행 및 출력 확인**

```bash
uv run python main.py
```

기대 출력:
```
=== Bitget Demo UTA 잔고 ===
  총 자산       : 50,000.00 USDT
  가용 증거금   : 50,000.00 USDT
  미실현 손익   :      0.00 USDT
```

오류 발생 시:
- `Invalid apikey` → `.env` 키 값 재확인
- `X-SIMULATED-TRADING` 관련 오류 → `src/client.py`의 `service.headers` 속성명을 SDK 소스에서 확인

- [ ] **Step 3: 최종 커밋**

```bash
git add .env.example
git commit -m "chore: add env example and complete stage 1"
```
