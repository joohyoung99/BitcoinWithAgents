from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def client(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    import dashboard
    (data_dir / "data").mkdir()
    monkeypatch.setattr(dashboard, "DATA", data_dir / "data")
    return TestClient(dashboard.app)


def test_state_empty(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json() == {}


def test_state_returns_btcusdt(client, data_dir: Path):
    payload = {"BTCUSDT": {"regime": "caution", "close": 81110.0}}
    (data_dir / "data" / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json()["regime"] == "caution"


def test_explorer_empty(client):
    r = client.get("/api/explorer")
    assert r.status_code == 200
    assert r.json() == {}


def test_positions_empty(client):
    r = client.get("/api/positions")
    assert r.status_code == 200
    assert r.json()["positions"] == []


def test_daily_empty(client):
    r = client.get("/api/daily")
    assert r.status_code == 200
    assert r.json()["trade_count"] == 0


def test_trades_empty(client):
    r = client.get("/api/trades")
    assert r.status_code == 200
    assert r.json() == []


def test_trades_returns_rows(client, data_dir: Path):
    csv_path = data_dir / "data" / "trades.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["opened_at", "realized_pnl_usdt"])
        writer.writeheader()
        writer.writerow({"opened_at": "2026-05-11T10:00:00Z", "realized_pnl_usdt": "50.0"})
    r = client.get("/api/trades")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["realized_pnl_usdt"] == "50.0"


def test_logs_empty(client):
    r = client.get("/api/logs")
    assert r.status_code == 200
    assert r.json()["lines"] == []


def test_logs_returns_last_100(client, data_dir: Path):
    lines = [f"line {i}" for i in range(150)]
    (data_dir / "data" / "system.log").write_text("\n".join(lines), encoding="utf-8")
    r = client.get("/api/logs")
    assert r.status_code == 200
    result = r.json()["lines"]
    assert len(result) == 100
    assert result[-1] == "line 149"
