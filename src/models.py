from dataclasses import dataclass


@dataclass
class MacroData:
    fed_funds_rate: float
    rate_trend: str  # "hiking" | "holding" | "cutting"


@dataclass
class ETFData:
    net_flow_3d: float
    flow_signal: str  # "inflow" | "outflow" | "neutral"


@dataclass
class FearGreedData:
    score: int
    label: str


@dataclass
class MarketData:
    funding_rate: float
    open_interest: float
    long_short_ratio: float


@dataclass
class ExplorerReport:
    timestamp: str
    risk: str  # "risk-on" | "risk-off"
    summary: str
    macro: MacroData | None
    etf: ETFData | None
    fear_greed: FearGreedData | None
    market: MarketData | None


@dataclass
class ChartSignal:
    symbol: str
    updated_at: str
    trend_range: str        # "trend" | "range"
    adx: float
    rsi: float
    ema_aligned: bool       # EMA20 > EMA50 > EMA200
    ema50_slope: float      # positive=up, negative=down
    atr: float
    close: float            # latest close price
    entry_signal: str       # "long" | "short" | "none"
    confidence: int         # 0–100
    signal_summary: str


@dataclass
class RegimeState:
    symbol: str
    regime: str           # "normal" | "caution" | "risk_off_trend" | "halt"
    prev_regime: str
    regime_changed: bool
    regime_transition: str  # "CAUTION_TO_NORMAL" | "" (empty if no change)
    regime_summary: str
    regime_updated_at: str
