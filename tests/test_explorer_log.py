from __future__ import annotations

from unittest.mock import patch

import pytest

import src.db as db_mod
from src.models import ExplorerReport, FearGreedData, MarketData


def test_run_once_logs_risk_event(tmp_path, patch_db, monkeypatch):
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

    inserts = [(sql, params) for sql, params in patch_db if "INSERT INTO events" in sql]
    assert inserts
    _, params = inserts[0]
    assert "INFO" in params
    assert "explorer" in params
    assert any("risk-on" in str(p) for p in params)
