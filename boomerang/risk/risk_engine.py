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
        """Marca o agente como travado (após liquidação flash)."""
        self._halted = True

    def clear_halt(self) -> None:
        """Destrava (reinício consciente do dono via /reiniciar). Limpa o circuit breaker."""
        self._halted = False

    # ── Dimensionamento de posição ───────────────────────────────────────────
    def position_size_usd(self, current_equity_usd: float, available_stable_usd: float,
                          override_pct: float | None = None) -> float:
        """Tamanho do trade, amigável a banca pequena.

        Automático: = % da banca (position_size_pct), com PISO operacional
        (min_position_usd) para não fazer trade "poeira" que o gás come, e TETO
        (max_position_pct) para não concentrar demais. Limitado pelo stable disponível.

        Manual (override_pct): o dono escolhe o tamanho explicitamente (até 100% =
        all-in), via confirmação no Telegram. Aqui o TETO automático NÃO se aplica
        (é decisão consciente); o piso e o stable disponível continuam valendo, e o
        disjuntor de drawdown segue ativo em can_open_position().

        Retorna 0.0 se nem o piso couber.
        """
        floor = self._cfg.min_position_usd
        if override_pct is not None:
            pct = max(0.0, min(float(override_pct), 100.0))
            target = max(current_equity_usd * (pct / 100.0), floor)
        else:
            target = max(current_equity_usd * (self._cfg.position_size_pct / 100.0), floor)
            target = min(target, current_equity_usd * (self._cfg.max_position_pct / 100.0))
        # BUFFER: nunca gastar 100% do stable. Trocar o saldo exato reverte com
        # "BEP20: transfer amount exceeds balance" por arredondamento/wobble de preço
        # entre a leitura e o swap. Deixa 3% de folga.
        size = min(target, available_stable_usd * 0.97)
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

        # SL/TP DESTA posição. Se stop_loss_pct > 0 = posição DINÂMICA (autônoma): usa o
        # stop calibrado e respeita tp=0 como "DEIXA CORRER" (saída assimétrica, só trailing).
        # Senão (manual/legado) cai no fixo do config. O trailing usa a distância do stop.
        if pos.stop_loss_pct > 0:
            stop_pct = pos.stop_loss_pct
            tp = pos.take_profit_pct  # 0 = sem teto (deixa o vencedor correr)
        else:
            stop_pct = self._cfg.user_stop_loss_pct
            tp = self._cfg.user_take_profit_pct

        # Lucro-alvo: bateu a meta de ganho → realiza o lucro.
        if tp > 0 and current_price >= pos.entry_price * (1.0 + tp / 100.0):
            return ExitSignal.SELL_TAKE_PROFIT

        trigger_price = pos.entry_price * (1.0 + self._cfg.trailing_trigger_pct / 100.0)

        if not pos.trailing_active and current_price >= trigger_price:
            pos.trailing_active = True
            pos.stop_loss_price = max(pos.stop_loss_price, pos.entry_price)  # break-even

        if pos.trailing_active:
            trailed = pos.peak_price * (1.0 - stop_pct / 100.0)
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


# ── SL/TP dinâmico por volatilidade (determinístico; R:R sempre >= 1:2) ───────
def dynamic_sl_tp(tier: str, var24h_abs: float) -> tuple[float, float]:
    """Stop-loss e take-profit (%) calibrados pela VOLATILIDADE do ativo na entrada.

    O cérebro classifica a tier (BAIXA/MEDIA/ALTA); a MATEMÁTICA é feita AQUI (LLM não
    faz conta). Relação Risco:Retorno travada em >= 1:2. var24h_abs = |percent_change_24h|.

      BAIXA  (|24h| <= 3%):  SL 2.0%            TP 4.0%   (1:2)
      MEDIA  (3-8%):         SL max(|24h|*.75, 3)  TP SL*2.0
      ALTA   (8-15%):        SL min(|24h|*.85, 7)  TP SL*2.5  (prêmio maior pelo risco)
    """
    t = (tier or "").strip().upper()
    if t in ("ALTA", "ALTO", "HIGH"):
        sl = max(min(var24h_abs * 0.85, 7.0), 3.0)
        tp = sl * 2.5
    elif t in ("MEDIA", "MÉDIA", "MEDIO", "MÉDIO", "MEDIUM", "MED"):
        sl = max(var24h_abs * 0.75, 3.0)
        tp = sl * 2.0
    else:  # BAIXA / LOW / desconhecido → conservador
        sl, tp = 2.0, 4.0
    # Garante R:R >= 1:2 mesmo após arredondar.
    sl = round(sl, 2)
    tp = round(max(tp, sl * 2.0), 2)
    return sl, tp


def tier_from_var(var24h_abs: float) -> str:
    """Fallback determinístico da tier a partir de |Var24h| (se o cérebro não classificar)."""
    if var24h_abs <= 3.0:
        return "BAIXA"
    if var24h_abs <= 8.0:
        return "MEDIA"
    return "ALTA"


# ── SKILL: Sizing por convicção (aposta escala com o confidence_score) ────────
def conviction_size_pct(base_pct: float, score: int, max_pct: float = 50.0) -> float:
    """Escala o tamanho da posição pela CONVICÇÃO do cérebro (confidence_score).
    Ancora no base_pct por volta do score 62 (corte típico); cresce +3%/ponto acima e
    encolhe abaixo, limitado entre 0,6x e 2,0x — e nunca acima do teto (max_pct).
    Concentra capital na vantagem real, sem virar all-in."""
    mult = 1.0 + (score - 62) * 0.03
    mult = max(0.6, min(mult, 2.0))
    return round(min(base_pct * mult, max_pct), 2)
