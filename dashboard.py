from __future__ import annotations

import csv
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="BTC Agent Monitor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_ROOT = Path(__file__).parent
DATA = _ROOT / "data"


@app.get("/")
def index():
    return FileResponse(_ROOT / "dashboard" / "index.html")


@app.get("/api/state")
def get_state():
    try:
        data = json.loads((DATA / "state.json").read_text(encoding="utf-8"))
        return data.get("BTCUSDT", {})
    except Exception:
        return {}


@app.get("/api/explorer")
def get_explorer():
    try:
        return json.loads((DATA / "explorer_report.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.get("/api/positions")
def get_positions():
    try:
        return json.loads((DATA / "positions.json").read_text(encoding="utf-8"))
    except Exception:
        return {"last_transition": "", "positions": []}


@app.get("/api/daily")
def get_daily():
    try:
        return json.loads((DATA / "daily.json").read_text(encoding="utf-8"))
    except Exception:
        return {"trade_count": 0, "wins": 0, "losses": 0, "consecutive_losses": 0, "daily_pnl_usdt": 0.0}


@app.get("/api/trades")
def get_trades():
    try:
        trades: list[dict] = []
        with (DATA / "trades.csv").open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                trades.append(dict(row))
        return trades
    except Exception:
        return []


@app.get("/api/portfolio")
def get_portfolio():
    try:
        return json.loads((DATA / "portfolio.json").read_text(encoding="utf-8"))
    except Exception:
        return {"equity": None, "unrealized": 0.0, "return_pct": None, "initial": 19950.0}


@app.get("/api/logs")
def get_logs():
    try:
        text = (DATA / "system.log").read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        return {"lines": lines[-100:]}
    except Exception:
        return {"lines": []}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=52090)
