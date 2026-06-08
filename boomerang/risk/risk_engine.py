"""Motor de risco do Boomerang AI.

REGRA DE OURO: nada aqui depende da IA. São travas matemáticas determinísticas
que protegem o capital e impedem a desclassificação no hackathon.

Responsabilidades:
  - Circuit breaker de drawdown global (sobre o pico de patrimônio).
  - Dimensionamento de posição (position sizing).
  - Mutex anti-loop (uma operação por vez).
  - Cooldown entre trades.
  - Stop-loss por trade + trailing stop.
  - Heartbeat (garante o mínimo de trades/dia do regulamento).

Matemática anti-DQ: com posição de 5% e stop de 5%, cada perda custa ~0,25%
da banca. Para chegar ao gatilho de segurança (~23%) seriam dezenas de perdas
seguidas — praticamente impossível. O circuit breaker é a última linha.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum

from boomerang.config import Config
from boomerang.types import Position, RejectReason


class ExitSignal(str, Enum):
    HOLD = "HOLD"
    SELL_STOP_LOSS = "SELL_STOP_LOSS"
    SELL_TRAILING = "SELL_TRAILING"
    SELL_TAKE_PROFIT = "SELL_TAKE_PROFIT"


@dataclass
class RiskDecision:
    allowed: bool
    reason: RejectReason | None = None
    detail: str = ""


class RiskEngine:
    """Estado de risco do agente. Thread-safe para o loop de monitoramento."""

    def __init__(self, config: Config, initial_equity_usd: float) -> None:
        self._cfg = config
        self._peak_equity = max(initial_equity_usd, 0.0)
        self._last_trade_ts: float = 0.0
        self._trade_lock = threading.Lock()  # mutex de execução (anti-loop)
        self._halted = False

    # ── Patrimônio / drawdown ────────────────────────────────────────────────
    def update_equity(self, current_equity_usd: float) -> None:
        """Atualiza o pico histórico (peak equity)."""
        if current_equity_usd > self._peak_equity:
            self._peak_equity = current_equity_usd

    def current_drawdown_pct(self, current_equity_usd: float) -> float:
        if self._peak_equity <= 0:
            return 0.0
        dd = (self._peak_equity - current_equity_usd) / self._peak_equity * 100.0
        return max(dd, 0.0)

    def circuit_breaker_tripped(self, current_equity_usd: float) -> bool:
        """True se o drawdown atingiu o gatilho de segurança (antes do DQ).

        Usa epsilon: num disjuntor de segurança, é melhor disparar um instante
        ANTES do limite do que depois (erro de ponto flutuante na borda exata).
        """
        return self.current_drawdown_pct(current_equity_usd) >= self._cfg.drawdown_safety_pct - 1e-9

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def last_trade_ts(self) -> float:
        return self._last_trade_ts

    def restore_state(self, peak_equity: float, last_trade_ts: float) -> None:
        """Restaura pico/último trade após reinício (persistência)."""
        if peak_equity and peak_equity > 0:
            self._peak_equity = peak_equity
        if last_trade_ts:
            self._last_trade_ts = last_trade_ts

    @property
    def halted(self) -> bool:
        return self._halted

    def halt(self) -> None:
        """Marca o agente como travado (após liquidação flash). Irreversível na sessão."""
        self._halted = True

    # ── Dimensionamento de posição ───────────────────────────────────────────
    def position_size_usd(self, current_equity_usd: float, available_stable_usd: float) -> float:
        """Tamanho do trade, amigável a banca pequena.

        = % da banca, mas com PISO operacional (min_position_usd) para não fazer
        trade "poeira" que o gás come, e TETO (max_position_pct) para não
        concentrar demais quando a banca é pequena. Limitado pelo stable disponível.
        Retorna 0.0 se nem o piso couber.
        """
        floor = self._cfg.min_position_usd
        target = max(current_equity_usd * (self._cfg.position_size_pct / 100.0), floor)
        target = min(target, current_equity_usd * (self._cfg.max_position_pct / 100.0))
        size = min(target, available_stable_usd)
        if size < floor:
            return 0.0
        return round(size, 6)

    # ── Permissão para abrir posição ─────────────────────────────────────────
    def can_open_position(
        self,
        *,
        current_equity_usd: float,
        available_stable_usd: float,
        open_positions: int,
        now_ts: float,
    ) -> RiskDecision:
        if self._halted:
            return RiskDecision(False, RejectReason.RISK_BLOCKED, "Agente travado (circuit breaker).")

        if current_equity_usd <= self._cfg.min_portfolio_usd:
            return RiskDecision(False, RejectReason.RISK_BLOCKED, "Patrimônio abaixo do mínimo.")

        if self.circuit_breaker_tripped(current_equity_usd):
            return RiskDecision(False, RejectReason.RISK_BLOCKED, "Drawdown no gatilho de segurança.")

        if open_positions >= self._cfg.max_concurrent_positions:
            return RiskDecision(False, RejectReason.MAX_POSITIONS, "Máximo de posições simultâneas.")

        if now_ts - self._last_trade_ts < self._cfg.trade_cooldown_seconds:
            restante = self._cfg.trade_cooldown_seconds - (now_ts - self._last_trade_ts)
            return RiskDecision(False, RejectReason.COOLDOWN, f"Cooldown: faltam {restante:.0f}s.")

        if self.position_size_usd(current_equity_usd, available_stable_usd) <= 0.0:
            return RiskDecision(False, RejectReason.RISK_BLOCKED, "Stable insuficiente p/ tamanho mínimo.")

        return RiskDecision(True)

    def record_trade(self, now_ts: float) -> None:
        self._last_trade_ts = now_ts

    # ── Mutex de execução (anti-loop / race condition) ───────────────────────
    @property
    def trade_lock(self) -> threading.Lock:
        return self._trade_lock

    # ── Stop-loss inicial de uma posição ─────────────────────────────────────
    def initial_stop_price(self, entry_price: float) -> float:
        return entry_price * (1.0 - self._cfg.user_stop_loss_pct / 100.0)

    def take_profit_price(self, entry_price: float) -> float:
        """Preço-alvo de lucro (0.0 se o lucro-alvo estiver desativado)."""
        tp = self._cfg.user_take_profit_pct
        return entry_price * (1.0 + tp / 100.0) if tp > 0 else 0.0

    # ── Avaliação contínua de uma posição (loop de 2s) ───────────────────────
    def evaluate_position(self, pos: Position, current_price: float) -> ExitSignal:
        """Decide se mantém ou sai. Atualiza pico/trailing da posição in-place.

        - Stop-loss: preço <= stop → vende.
        - Trailing: ao subir o gatilho (ex.: +5%), move o stop p/ break-even e
          passa a acompanhar o pico (ratchet), nunca descendo o stop.
        """
        if current_price > pos.peak_price:
            pos.peak_price = current_price

        # Lucro-alvo: bateu a meta de ganho do usuário → realiza o lucro.
        tp = self._cfg.user_take_profit_pct
        if tp > 0 and current_price >= pos.entry_price * (1.0 + tp / 100.0):
            return ExitSignal.SELL_TAKE_PROFIT

        trigger_price = pos.entry_price * (1.0 + self._cfg.trailing_trigger_pct / 100.0)

        if not pos.trailing_active and current_price >= trigger_price:
            pos.trailing_active = True
            pos.stop_loss_price = max(pos.stop_loss_price, pos.entry_price)  # break-even

        if pos.trailing_active:
            trailed = pos.peak_price * (1.0 - self._cfg.user_stop_loss_pct / 100.0)
            pos.stop_loss_price = max(pos.stop_loss_price, trailed)

        if current_price <= pos.stop_loss_price:
            return ExitSignal.SELL_TRAILING if pos.trailing_active else ExitSignal.SELL_STOP_LOSS

        return ExitSignal.HOLD

    # ── Heartbeat (mínimo de trades do regulamento) ──────────────────────────
    def needs_heartbeat(self, now_ts: float) -> bool:
        """True se passou tempo demais sem operar e precisamos de um trade de manutenção."""
        if self._last_trade_ts == 0.0:
            return False  # ainda não começou a operar nesta sessão
        horas = (now_ts - self._last_trade_ts) / 3600.0
        return horas >= self._cfg.heartbeat_after_hours
