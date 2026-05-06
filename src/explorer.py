from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import requests
from google import genai

from src.models import (
    ETFData,
    ExplorerReport,
    FearGreedData,
    MacroData,
    MarketData,
)

FEAR_GREED_URL = "https://api.alternative.me/fng/"
BITGET_BASE = "https://api.bitget.com"
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
SOSOVALUE_BASE = "https://api.sosovalue.com"  # SoSoValue 문서 확인 후 조정

REPORT_PATH = Path("data/explorer_report.json")


def collect_fear_greed() -> FearGreedData | None:
    try:
        resp = requests.get(FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        item = resp.json()["data"][0]
        return FearGreedData(
            score=int(item["value"]),
            label=item["value_classification"],
        )
    except Exception as e:
        print(f"[explorer] fear_greed collection failed: {e}")
        return None
