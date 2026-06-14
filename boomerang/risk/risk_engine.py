"""Boomerang AI risk engine.

GOLDEN RULE: nothing here depends on the AI. These are deterministic mathematical
locks that protect capital and prevent disqualification in the hackathon.

Responsibilities:
  - Global drawdown circuit breaker (over peak equity).
  - Position sizing.
  - Anti-loop mutex (one operation at a time).
  - Cooldown between trades.
  - Per-trade stop-loss + trailing stop.
  - Heartbeat (ensures the minimum trades/day from the rules).

Anti-DQ math: with a 5% position and a 5% stop, each loss costs ~0.25%
of the bankroll. To reach the safety trigger (~23%) would take dozens of losses
in a row — practically impossible. The circuit breaker is the last line.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum

from boomerang.config import Config
from boomerang.types import Position, RejectReason


class ExitSignal(str, Enum):
    HOLD = "HOLD"
    SELL_STOP_LOSS = "SELL_STOP_LOSS"
    SELL_TRAILING = "SELL_TRAILING"
    SELL_TAKE_PROFIT = "SELL_TAKE_PROFIT"
    SELL_TIME_STALE = "SELL_TIME_STALE"  # idle capital: no progress for too long


@dataclass
class RiskDecision:
    allowed: bool
    reason: RejectReason | None = None
    detail: str = ""


class RiskEngine:
    """Agent risk state. Thread-safe for the monitoring loop."""

    def __init__(self, config: Config, initial_equity_usd: float) -> None:
        self._cfg = config
        self._peak_equity = max(initial_equity_usd, 0.0)
        self._last_trade_ts: float = 0.0
        self._trade_lock = threading.Lock()  # execution mutex (anti-loop)
        self._halted = False
        self._day_anchor_equity = max(initial_equity_usd, 0.0)  # equity at the start of the day (UTC)
        self._day_anchor_idx: int | None = None                 # current day bucket

    # ── Equity / drawdown ────────────────────────────────────────────────────
    def update_equity(self, current_equity_usd: float, now_ts: float = 0.0) -> None:
        """Updates the all-time peak equity and the daily anchor (daily loss cap).

        The daily anchor resets when the day rolls over (UTC): from then on, the daily
        loss cap measures the drop relative to this opening equity.
        """
        if current_equity_usd > self._peak_equity:
            self._peak_equity = current_equity_usd
        day = int((now_ts or time.time()) // 86400)
        if day != self._day_anchor_idx:
            self._day_anchor_idx = day
            self._day_anchor_equity = max(current_equity_usd, 0.0)

    def current_drawdown_pct(self, current_equity_usd: float) -> float:
        if self._peak_equity <= 0:
            return 0.0
        dd = (self._peak_equity - current_equity_usd) / self._peak_equity * 100.0
        return max(dd, 0.0)

    def circuit_breaker_tripped(self, current_equity_usd: float) -> bool:
        """True if the drawdown reached the safety trigger (before the DQ).

        Uses an epsilon: in a safety circuit breaker, it is better to trip an instant
        BEFORE the limit than after (floating-point error at the exact edge).
        """
        return self.current_drawdown_pct(current_equity_usd) >= self._cfg.drawdown_safety_pct - 1e-9

    def daily_drawdown_pct(self, current_equity_usd: float) -> float:
        """Drop (%) relative to the day's opening equity (UTC)."""
        if self._day_anchor_equity <= 0:
            return 0.0
        dd = (self._day_anchor_equity - current_equity_usd) / self._day_anchor_equity * 100.0
        return max(dd, 0.0)

    def daily_loss_tripped(self, current_equity_usd: float) -> bool:
        """True if the loss FOR THE DAY reached the limit (intraday cap). 0 = disabled.

        Complements the all-time-peak circuit breaker: catches a single bad day that has
        not yet reached the global 23% trigger over the all-time peak.
        """
        cap = self._cfg.daily_loss_cap_pct
        if cap <= 0:
            return False
        return self.daily_drawdown_pct(current_equity_usd) >= cap - 1e-9

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def last_trade_ts(self) -> float:
        return self._last_trade_ts

    def restore_state(self, peak_equity: float, last_trade_ts: float) -> None:
        """Restores peak/last trade after a restart (persistence)."""
        if peak_equity and peak_equity > 0:
            self._peak_equity = peak_equity
        if last_trade_ts:
            self._last_trade_ts = last_trade_ts

    @property
    def halted(self) -> bool:
        return self._halted

    def halt(self) -> None:
        """Marks the agent as halted (after a flash liquidation)."""
        self._halted = True

    def clear_halt(self) -> None:
        """Unhalts (deliberate owner restart via /reiniciar). Clears the circuit breaker."""
        self._halted = False

    # ── Position sizing ──────────────────────────────────────────────────────
    def position_size_usd(self, current_equity_usd: float, available_stable_usd: float,
                          override_pct: float | None = None) -> float:
        """Trade size, friendly to a small bankroll.

        Automatic: = % of the bankroll (position_size_pct), with an operational FLOOR
        (min_position_usd) so as not to make a "dust" trade eaten by gas, and a CAP
        (max_position_pct) so as not to concentrate too much. Limited by the available stable.

        Manual (override_pct): the owner explicitly chooses the size (up to 100% =
        all-in), via confirmation on Telegram. Here the automatic CAP does NOT apply
        (it is a deliberate decision); the floor and the available stable still hold, and the
        drawdown circuit breaker remains active in can_open_position().

        Returns 0.0 if not even the floor fits.
        """
        floor = self._cfg.min_position_usd
        if override_pct is not None:
            pct = max(0.0, min(float(override_pct), 100.0))
            target = max(current_equity_usd * (pct / 100.0), floor)
        else:
            target = max(current_equity_usd * (self._cfg.position_size_pct / 100.0), floor)
            target = min(target, current_equity_usd * (self._cfg.max_position_pct / 100.0))
        # BUFFER: never spend 100% of the stable. Swapping the exact balance reverts with
        # "BEP20: transfer amount exceeds balance" due to rounding/price wobble
        # between the read and the swap. Leaves 3% of slack.
        size = min(target, available_stable_usd * 0.97)
        if size < floor:
            return 0.0
        return round(size, 6)

    # ── Permission to open a position ────────────────────────────────────────
    def can_open_position(
        self,
        *,
        current_equity_usd: float,
        available_stable_usd: float,
        open_positions: int,
        now_ts: float,
    ) -> RiskDecision:
        if self._halted:
            return RiskDecision(False, RejectReason.RISK_BLOCKED, "Agent halted (circuit breaker).")

        if current_equity_usd <= self._cfg.min_portfolio_usd:
            return RiskDecision(False, RejectReason.RISK_BLOCKED, "Equity below the minimum.")

        if self.circuit_breaker_tripped(current_equity_usd):
            return RiskDecision(False, RejectReason.RISK_BLOCKED, "Drawdown at the safety trigger.")

        if open_positions >= self._cfg.max_concurrent_positions:
            return RiskDecision(False, RejectReason.MAX_POSITIONS, "Maximum simultaneous positions.")

        if now_ts - self._last_trade_ts < self._cfg.trade_cooldown_seconds:
            restante = self._cfg.trade_cooldown_seconds - (now_ts - self._last_trade_ts)
            return RiskDecision(False, RejectReason.COOLDOWN, f"Cooldown: {restante:.0f}s remaining.")

        if self.position_size_usd(current_equity_usd, available_stable_usd) <= 0.0:
            return RiskDecision(False, RejectReason.RISK_BLOCKED, "Insufficient stable for minimum size.")

        return RiskDecision(True)

    def record_trade(self, now_ts: float) -> None:
        self._last_trade_ts = now_ts

    # ── Execution mutex (anti-loop / race condition) ─────────────────────────
    @property
    def trade_lock(self) -> threading.Lock:
        return self._trade_lock

    # ── Initial stop-loss of a position ──────────────────────────────────────
    def initial_stop_price(self, entry_price: float) -> float:
        return entry_price * (1.0 - self._cfg.user_stop_loss_pct / 100.0)

    def take_profit_price(self, entry_price: float) -> float:
        """Target profit price (0.0 if the take-profit is disabled)."""
        tp = self._cfg.user_take_profit_pct
        return entry_price * (1.0 + tp / 100.0) if tp > 0 else 0.0

    # ── Continuous evaluation of a position (2s loop) ────────────────────────
    def evaluate_position(self, pos: Position, current_price: float, tighten: bool = False) -> ExitSignal:
        """Decides whether to hold or exit, with the position's STRATEGY parameters.

        tighten=True (EXTREME GREED in the market): tightens the trailing — activates earlier and
        follows closer — to LOCK the profit before the reversal of the euphoric top. It is the
        symmetric side of F&G: "be fearful when everyone is greedy" (protects the gain at the top).
        Updates peak/trailing in-place. Each field falls back to the config global if = 0
        (manual/legacy buys), so the old behavior is preserved.

        Order: fixed TP → trailing (trigger/ratchet) → SL → time-stop.
          • Fixed TP (mean-rev/dca): sells when it hits the target.
          • Trailing (momentum): after the trigger, moves the stop to break-even and follows the peak.
          • SL: only fires if there is a stop_loss_price (DCA = no fixed SL → 0 → ignored).
          • Time-stop: by dead band (momentum 20min) or by pure TIME (dca 24h, band 999).
        """
        if current_price > pos.peak_price:
            pos.peak_price = current_price

        # Parameters of THIS position (0 = fallback to the config global).
        stop_pct = pos.stop_loss_pct if pos.stop_loss_pct > 0 else self._cfg.user_stop_loss_pct
        tp = pos.take_profit_pct if pos.take_profit_pct > 0 else (
            0.0 if pos.stop_loss_pct > 0 else self._cfg.user_take_profit_pct)
        trail_trigger = (pos.trailing_trigger_pct if pos.trailing_trigger_pct > 0
                         else self._cfg.trailing_trigger_pct)
        trail_dist = pos.trailing_pct if pos.trailing_pct > 0 else stop_pct
        if tighten and trail_trigger > 0:
            trail_trigger *= 0.5   # activates the trailing with half the profit (locks earlier)
            trail_dist = max(trail_dist * 0.6, 0.5)  # follows closer to the peak (floor 0.5%)

        # 1) FIXED take-profit: hit the target → realize.
        if tp > 0 and current_price >= pos.entry_price * (1.0 + tp / 100.0):
            return ExitSignal.SELL_TAKE_PROFIT

        # 2) Trailing: on crossing the profit trigger, raises the stop to break-even and follows the peak.
        if trail_trigger > 0:
            if not pos.trailing_active and current_price >= pos.entry_price * (1.0 + trail_trigger / 100.0):
                pos.trailing_active = True
                pos.stop_loss_price = max(pos.stop_loss_price, pos.entry_price)  # break-even
            if pos.trailing_active:
                trailed = pos.peak_price * (1.0 - trail_dist / 100.0)
                pos.stop_loss_price = max(pos.stop_loss_price, trailed)

        # 3) Stop-loss: only if there is a stop (DCA opens without SL → stop_loss_price = 0 → skipped).
        if pos.stop_loss_price > 0 and current_price <= pos.stop_loss_price:
            return ExitSignal.SELL_TRAILING if pos.trailing_active else ExitSignal.SELL_STOP_LOSS

        # 4) Time-stop: position WITHOUT active trailing, open for too long. By dead band
        # (idle capital) OR by pure time (DCA: band 999 → exits on schedule whatever the PnL).
        ts_min = pos.time_stop_min if pos.time_stop_min > 0 else self._cfg.max_hold_hours * 60.0
        ts_band = pos.time_stop_band_pct if pos.time_stop_band_pct > 0 else self._cfg.stale_pnl_band_pct
        if (not pos.trailing_active and ts_min > 0 and pos.opened_at
                and (time.time() - pos.opened_at) / 60.0 >= ts_min):
            pnl = ((current_price - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price else 0.0
            if abs(pnl) <= ts_band:
                return ExitSignal.SELL_TIME_STALE

        return ExitSignal.HOLD

    # ── Heartbeat (minimum trades from the rules) ────────────────────────────
    def needs_heartbeat(self, now_ts: float) -> bool:
        """True if too much time has passed without trading and we need a maintenance trade."""
        if self._last_trade_ts == 0.0:
            return False  # has not started trading in this session yet
        horas = (now_ts - self._last_trade_ts) / 3600.0
        return horas >= self._cfg.heartbeat_after_hours


# ── Dynamic SL/TP by volatility (deterministic; R:R always >= 1:2) ────────────
def dynamic_sl_tp(tier: str, var24h_abs: float) -> tuple[float, float]:
    """Stop-loss and take-profit (%) calibrated by the asset's VOLATILITY at entry.

    The brain classifies the tier (BAIXA/MEDIA/ALTA); the MATH is done HERE (the LLM does
    not do arithmetic). Risk:Reward locked at >= 1:2. var24h_abs = |percent_change_24h|.

      BAIXA  (|24h| <= 3%):  SL 2.0%            TP 4.0%   (1:2)
      MEDIA  (3-8%):         SL max(|24h|*.75, 3)  TP SL*2.0
      ALTA   (8-15%):        SL min(|24h|*.85, 7)  TP SL*2.5  (larger premium for the risk)
    """
    t = (tier or "").strip().upper()
    if t in ("ALTA", "ALTO", "HIGH"):
        sl = max(min(var24h_abs * 0.85, 7.0), 3.0)
        tp = sl * 2.5
    elif t in ("MEDIA", "MÉDIA", "MEDIO", "MÉDIO", "MEDIUM", "MED"):
        sl = max(var24h_abs * 0.75, 3.0)
        tp = sl * 2.0
    else:  # BAIXA / LOW / unknown → conservative
        sl, tp = 2.0, 4.0
    # Ensures R:R >= 1:2 even after rounding.
    sl = round(sl, 2)
    tp = round(max(tp, sl * 2.0), 2)
    return sl, tp


def tier_from_var(var24h_abs: float) -> str:
    """Deterministic fallback of the tier from |Var24h| (if the brain does not classify)."""
    if var24h_abs <= 3.0:
        return "BAIXA"
    if var24h_abs <= 8.0:
        return "MEDIA"
    return "ALTA"


# ── SKILL: Adaptation by regime (the stance changes with the market) ──────────
def market_regime(btc_24h: float | None, fng: int | None = None,
                  funding: float | None = None) -> tuple[str, int]:
    """Reads the market REGIME and returns (label, cutoff-adjustment). The adjustment shifts the
    entry bar: BULL lowers it (more aggressive); DEFENSIVO raises it (more selective).

    Combines THREE readings:
      • PRICE (BTC 24h): >= +3% → BULL (-5) · <= -2% → DEFENSIVO (+8) · otherwise NEUTRO.
      • SENTIMENT (Fear & Greed 0-100): extreme greed (>=78) raises the bar (+5, avoids
        the euphoric top); extreme fear (<=22) raises the bar (+4, risk-off).
      • LEVERAGE (BTC perp funding rate, per 8h): >= +0.05% → over-leveraged longs,
        flush risk → raises the bar (+6, defensive); <= -0.02% → crowded shorts,
        upward squeeze bias → lowers it slightly (-3, mild contrarian risk-on).
      The extremes call for more caution — aligned with capital protection."""
    if btc_24h is None:
        label, adj = "NEUTRO", 0
    elif btc_24h >= 3.0:
        label, adj = "BULL", -5
    elif btc_24h <= -2.0:
        label, adj = "DEFENSIVO", 8
    else:
        label, adj = "NEUTRO", 0
    if fng is not None:
        if fng >= 78:
            label, adj = f"{label}/GANÂNCIA", adj + 5
        elif fng <= 22:
            label, adj = f"{label}/MEDO", adj + 4
    if funding is not None:
        if funding >= 0.0005:
            label, adj = f"{label}/ALAVANCADO", adj + 6
        elif funding <= -0.0002:
            label, adj = f"{label}/SHORTS", adj - 3
    return label, adj


# ── Anti-top LOCK: dampens the size when the asset has already risen too much ──
def overextension_factor(ch24h: float) -> float:
    """Factor 0.5–1.0 that REDUCES the size when the 24h has already overextended (top risk).
    Deterministic (does not depend on the LLM): up to +10% it does nothing; from +10% to +25% it
    falls linearly to half. Above the cap (max_entry_24h_pct) the entry is REFUSED in the agent."""
    if ch24h <= 10.0:
        return 1.0
    return max(0.5, 1.0 - (ch24h - 10.0) / 30.0)


# ── SKILL: Conviction sizing (the bet scales with the confidence_score) ───────
def conviction_size_pct(base_pct: float, score: int, max_pct: float = 50.0) -> float:
    """Scales the position size by the brain's CONVICTION (confidence_score).
    Anchors at base_pct around score 62 (typical cutoff); grows +3%/point above and
    shrinks below, capped between 0.6x and 2.0x — and never above the cap (max_pct).
    Concentrates capital on the real edge, without becoming all-in."""
    mult = 1.0 + (score - 62) * 0.03
    mult = max(0.6, min(mult, 2.0))
    return round(min(base_pct * mult, max_pct), 2)


# ── Integrity of the equity reading (anti false-circuit-breaker) ──────────────
def equity_reading_reliable(holdings: list, position_symbols) -> bool:
    """True if EVERY open position appears PRICED (value_usd > 0) in the breakdown.

    If a position we know we hold was not priced (limited RPC / no price route),
    the equity comes back DEFLATED and MUST NOT trip the drawdown circuit breaker — otherwise a
    price hiccup would liquidate the wallet by mistake."""
    priced = {str(h.get("symbol", "")).upper()
              for h in holdings if (h.get("value_usd") or 0) > 0}
    return all(str(s).upper() in priced for s in position_symbols)
