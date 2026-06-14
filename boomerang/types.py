"""Shared types across the Boomerang AI processes and filters."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"


class RejectReason(str, Enum):
    LOW_CONFIDENCE = "REJECTED_LOW_CONFIDENCE"
    NOT_WHITELISTED = "REJECTED_NOT_WHITELISTED"
    BURNING_TAX = "REJECTED_BURNING_TAX"
    ORACLE_DESYNC = "REJECTED_ORACLE_DESYNC"
    HIGH_SLIPPAGE = "REJECTED_HIGH_SLIPPAGE"
    NO_LIQUIDITY = "REJECTED_NO_LIQUIDITY"
    RISK_BLOCKED = "REJECTED_RISK_BLOCKED"
    COOLDOWN = "REJECTED_COOLDOWN"
    MAX_POSITIONS = "REJECTED_MAX_POSITIONS"


class AgentState(str, Enum):
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    IN_POSITION = "IN_POSITION"
    PAUSED = "PAUSED"
    HALTED = "HALTED"  # circuit breaker tripped — read-only


@dataclass
class Verdict:
    """Output of Filter 1 (CMC/LLM brain)."""

    symbol: str
    confidence_score: int
    action: Action
    rationale: str
    volatility: str = ""  # tier classified by the brain: BAIXA | MEDIA | ALTA (for dynamic SL/TP)
    regime: str = ""      # regime read by the brain: uptrend | choppy | downtrend (for win-rate by regime)

    @property
    def is_buy(self) -> bool:
        return self.action == Action.BUY


@dataclass
class ValidationResult:
    """Output of Filter 2 (on-chain validation)."""

    ok: bool
    symbol: str
    token_address: str | None = None
    estimated_slippage_pct: float | None = None
    oracle_divergence_pct: float | None = None
    expected_out: int | None = None        # in the token's base units (wei)
    min_out: int | None = None             # amountOutMin with slippage applied
    reason: RejectReason | None = None
    detail: str = ""


@dataclass
class TradeIntent:
    """Buy intent that crosses customs through to Filter 3."""

    symbol: str
    token_address: str
    amount_usd: float
    max_slippage_pct: float
    user_stop_loss_pct: float
    min_out: int | None = None


@dataclass
class ExecutionResult:
    """Output of Filter 3 (TWAK execution)."""

    ok: bool
    symbol: str
    tx_hash: str | None = None
    entry_price: float | None = None
    qty: float | None = None
    error: str = ""


@dataclass
class Position:
    """Open position monitored by the risk/execution engine."""

    symbol: str
    token_address: str
    entry_price: float
    amount_usd: float
    qty: float
    stop_loss_price: float
    stop_loss_pct: float = 0.0      # dynamic SL for this position (0 = uses the fixed one from config)
    take_profit_pct: float = 0.0    # dynamic TP for this position (0 = uses the fixed one from config)
    trailing_active: bool = False
    peak_price: float = 0.0
    opened_at: float = 0.0
    tx_hash: str | None = None
    regime: str = ""                # regime at ENTRY (uptrend/choppy/downtrend) for win-rate by regime
    strategy: str = ""              # strategy that opened it: momentum | mean_reversion | dca
    trailing_trigger_pct: float = 0.0  # profit to activate trailing (0 = uses the global one from config)
    trailing_pct: float = 0.0       # trailing distance (0 = uses stop_loss_pct)
    time_stop_min: float = 0.0      # minutes for time-stop (0 = uses the global one from config)
    time_stop_band_pct: float = 0.0  # dead band of the time-stop (0 = uses the global; 999 = pure time)

    def __post_init__(self) -> None:
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price
