from __future__ import annotations

import pytest

import src.db as db_mod


def test_init_db_creates_tables(patch_db):
    db_mod.init_db()
    sqls = [sql for sql, _ in patch_db]
    assert any("api_logs" in sql for sql in sqls)
    assert any("llm_logs" in sql for sql in sqls)
    assert any("events" in sql for sql in sqls)


def test_log_api_inserts_row(patch_db):
    db_mod.log_api("/test/endpoint", {"key": "val"}, {"code": "00000"}, 123)
    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO api_logs" in sql]
    assert inserts
    _, params = inserts[0]
    assert "/test/endpoint" in params
    assert "00000" in params
    assert 123 in params


def test_log_llm_inserts_row(patch_db):
    db_mod.log_llm("test_module", "gemini-2.5-flash", "hello prompt", "hello response", 456, success=True)
    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO llm_logs" in sql]
    assert inserts
    _, params = inserts[0]
    assert "test_module" in params
    assert "gemini-2.5-flash" in params
    assert 1 in params  # success=True → 1


def test_log_event_inserts_row(patch_db):
    db_mod.log_event("INFO", "regime", "normal → caution", extra={"symbol": "BTCUSDT"})
    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO events" in sql]
    assert inserts
    _, params = inserts[0]
    assert "INFO" in params
    assert "regime" in params
    assert "normal → caution" in params


def test_log_api_does_not_raise_on_connection_error(monkeypatch):
    def failing_connect():
        raise Exception("connection refused")
    monkeypatch.setattr(db_mod, "_connect", failing_connect)
    db_mod.log_api("/test", {}, None, 0)  # must not raise
