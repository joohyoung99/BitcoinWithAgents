from __future__ import annotations

import json
import os
import time

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _connect():
    return psycopg2.connect(DATABASE_URL, options="-c client_encoding=UTF8")


def init_db() -> None:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS api_logs (
                id            SERIAL PRIMARY KEY,
                timestamp     TEXT NOT NULL,
                endpoint      TEXT NOT NULL,
                request_json  TEXT,
                response_json TEXT,
                status_code   TEXT,
                duration_ms   INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS llm_logs (
                id             SERIAL PRIMARY KEY,
                timestamp      TEXT NOT NULL,
                module         TEXT NOT NULL,
                model          TEXT NOT NULL,
                prompt_preview TEXT,
                response_text  TEXT,
                duration_ms    INTEGER,
                success        INTEGER NOT NULL DEFAULT 1
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id         SERIAL PRIMARY KEY,
                timestamp  TEXT NOT NULL,
                level      TEXT NOT NULL,
                module     TEXT NOT NULL,
                message    TEXT NOT NULL,
                extra_json TEXT
            )
        """)
        conn.commit()
        cur.close()
    finally:
        conn.close()


def log_api(
    endpoint: str,
    request_body: dict,
    response_body: dict | None,
    duration_ms: int,
) -> None:
    try:
        status_code = (response_body or {}).get("code", "ERR")
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO api_logs "
                "(timestamp, endpoint, request_json, response_json, status_code, duration_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    endpoint,
                    json.dumps(request_body, ensure_ascii=False),
                    json.dumps(response_body, ensure_ascii=False) if response_body is not None else None,
                    status_code,
                    duration_ms,
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
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
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO llm_logs "
                "(timestamp, module, model, prompt_preview, response_text, duration_ms, success) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
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
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        print(f"[db] log_llm failed: {e}")


def log_event(
    level: str,
    module: str,
    message: str,
    extra: dict | None = None,
) -> None:
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO events (timestamp, level, module, message, extra_json) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    level,
                    module,
                    message,
                    json.dumps(extra, ensure_ascii=False) if extra is not None else None,
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        print(f"[db] log_event failed: {e}")
