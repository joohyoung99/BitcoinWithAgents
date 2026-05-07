from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import src.db as db_mod


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test_trading.db")


def test_init_db_creates_tables():
    db_mod.init_db()
    conn = sqlite3.connect(db_mod.DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert {"api_logs", "llm_logs", "events"} <= tables


def test_log_api_inserts_row():
    db_mod.init_db()
    db_mod.log_api("/test/endpoint", {"key": "val"}, {"code": "00000"}, 123)
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT endpoint, status_code, duration_ms FROM api_logs").fetchone()
    conn.close()
    assert row == ("/test/endpoint", "00000", 123)


def test_log_llm_inserts_row():
    db_mod.init_db()
    db_mod.log_llm("test_module", "gemini-2.5-flash", "hello prompt", "hello response", 456, success=True)
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT module, model, success FROM llm_logs").fetchone()
    conn.close()
    assert row == ("test_module", "gemini-2.5-flash", 1)


def test_log_event_inserts_row():
    db_mod.init_db()
    db_mod.log_event("INFO", "regime", "normal → caution", extra={"symbol": "BTCUSDT"})
    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT level, module, message FROM events").fetchone()
    conn.close()
    assert row == ("INFO", "regime", "normal → caution")


def test_log_api_does_not_raise_on_bad_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", Path("/nonexistent_dir/bad.db"))
    db_mod.log_api("/test", {}, None, 0)  # must not raise
