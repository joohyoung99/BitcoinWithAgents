from __future__ import annotations

import sqlite3
from unittest.mock import patch

import src.db as db_mod
from src.models import ExplorerReport, FearGreedData, MarketData


def test_run_once_logs_risk_event(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db()

    import src.explorer as explorer_mod
    monkeypatch.setattr(explorer_mod, "REPORT_PATH", tmp_path / "report.json")

    fg = FearGreedData(score=70, label="Greed")
    market = MarketData(funding_rate=0.005, open_interest=1e9, long_short_ratio=1.2)
    report = ExplorerReport(
        timestamp="2026-05-07T00:00:00+00:00",
        risk="risk-on",
        summary="테스트",
        macro=None,
        etf=None,
        fear_greed=fg,
        market=market,
    )

    with patch("src.explorer.collect_macro", return_value=None), \
         patch("src.explorer.collect_etf", return_value=None), \
         patch("src.explorer.collect_fear_greed", return_value=fg), \
         patch("src.explorer.collect_market", return_value=market), \
         patch("src.explorer.analyse", return_value=report):
        explorer_mod.run_once()

    conn = sqlite3.connect(db_mod.DB_PATH)
    row = conn.execute("SELECT level, module, message FROM events").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "INFO"
    assert row[1] == "explorer"
    assert "risk-on" in row[2]
