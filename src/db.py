from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("data/trading.db")


def init_db() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                endpoint      TEXT NOT NULL,
                request_json  TEXT,
                response_json TEXT,
                status_code   TEXT,
                duration_ms   INTEGER
            );
            CREATE TABLE IF NOT EXISTS llm_logs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp      TEXT NOT NULL,
                module         TEXT NOT NULL,
                model          TEXT NOT NULL,
                prompt_preview TEXT,
                response_text  TEXT,
                duration_ms    INTEGER,
                success        INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT NOT NULL,
                level      TEXT NOT NULL,
                module     TEXT NOT NULL,
                message    TEXT NOT NULL,
                extra_json TEXT
            );
        """)


def log_api(
    endpoint: str,
    request_body: dict,
    response_body: dict | None,
    duration_ms: int,
) -> None:
    try:
        status_code = (response_body or {}).get("code", "ERR") if response_body else "ERR"
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO api_logs "
                "(timestamp, endpoint, request_json, response_json, status_code, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    endpoint,
                    json.dumps(request_body, ensure_ascii=False),
                    json.dumps(response_body, ensure_ascii=False) if response_body is not None else None,
                    status_code,
                    duration_ms,
                ),
            )
    except Exception as e:
        print(f"[db] log_api failed: {e}")


def log_llm(
    module: str,
    model: str,
    prompt: str,
    response_text: str,
    duration_ms: int,
    success: bool = True,
) -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO llm_logs "
                "(timestamp, module, model, prompt_preview, response_text, duration_ms, success) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    module,
                    model,
                    prompt[:200],
                    response_text,
                    duration_ms,
                    1 if success else 0,
                ),
            )
    except Exception as e:
        print(f"[db] log_llm failed: {e}")


def log_event(
    level: str,
    module: str,
    message: str,
    extra: dict | None = None,
) -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO events (timestamp, level, module, message, extra_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    level,
                    module,
                    message,
                    json.dumps(extra, ensure_ascii=False) if extra is not None else None,
                ),
            )
    except Exception as e:
        print(f"[db] log_event failed: {e}")
