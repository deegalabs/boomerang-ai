"""Tipos compartilhados entre os processos e filtros do Boomerang AI."""
from __future__ import annotations

from dataclasses import dataclass, field
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
    HALTED = "HALTED"  # circuit breaker disparado — só leitura


@dataclass
class Verdict:
    """Saída do Filtro 1 (cérebro CMC/LLM)."""

    symbol: str
    confidence_score: int
    action: Action
    rationale: str

    @property
    def is_buy(self) -> bool:
        return self.action == Action.BUY


@dataclass
class ValidationResult:
    """Saída do Filtro 2 (validação on-chain)."""

    ok: bool
    symbol: str
    token_address: str | None = None
    estimated_slippage_pct: float | None = None
    oracle_divergence_pct: float | None = None
    expected_out: int | None = None        # em unidades base do token (wei)
    min_out: int | None = None             # amountOutMin com slippage aplicado
    reason: RejectReason | None = None
    detail: str = ""


@dataclass
class TradeIntent:
    """Intenção de compra que atravessa a alfândega até o Filtro 3."""

    symbol: str
    token_address: str
    amount_usd: float
    max_slippage_pct: float
    user_stop_loss_pct: float
    min_out: int | None = None


@dataclass
class ExecutionResult:
    """Saída do Filtro 3 (execução TWAK)."""

    ok: bool
    symbol: str
    tx_hash: str | None = None
    entry_price: float | None = None
    qty: float | None = None
    error: str = ""


@dataclass
class Position:
    """Posição aberta monitorada pelo motor de risco/execução."""

    symbol: str
    token_address: str
    entry_price: float
    amount_usd: float
    qty: float
    stop_loss_price: float
    trailing_active: bool = False
    peak_price: float = 0.0
    opened_at: float = 0.0
    tx_hash: str | None = None

    def __post_init__(self) -> None:
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price
