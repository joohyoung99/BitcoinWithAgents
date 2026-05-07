from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google import genai

from src.models import RegimeState

STATE_PATH = Path("data/state.json")
STALE_REGIME_MIN = 30

_VALID_REGIMES = {"normal", "caution", "risk_off_trend", "halt"}

_REGIME_TABLE: dict[tuple[str, str], str] = {
    ("trend", "risk-on"):  "normal",
    ("range", "risk-on"):  "caution",
    ("trend", "risk-off"): "risk_off_trend",
    ("range", "risk-off"): "halt",
}


def classify_regime(trend_range: str, risk: str) -> str:
    return _REGIME_TABLE.get((trend_range, risk), "halt")
