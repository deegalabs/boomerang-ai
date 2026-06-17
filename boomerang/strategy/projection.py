"""Per-trade EV projection — what the agent ESTIMATES it can reach with an entry.

Pure and stdlib-only. Turns a strategy's target/stop + the brain conviction (and ATR when the
target is open-ended/trailing) into a transparent expected-value view:

  * target_pct   — the projected reachable move (the fixed TP, or an ATR-derived move when the
                   strategy rides a trailing stop)
  * breakeven_win— the win rate REQUIRED to break even given target/stop and round-trip fees
  * est_win      — an estimated win rate from the brain conviction (a documented heuristic)
  * ev_pct       — the resulting expected value per trade, net of fees
  * favorable    — ev_pct > 0

It is an ESTIMATE surfaced to the user (on /live and Telegram) and an OPTIONAL entry filter
(`target_return_pct`) — never a promise of return. The deterministic risk engine still owns
the actual exits; this only *describes and screens* a candidate.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeProjection:
    target_pct: float       # projected reachable move %
    stop_pct: float         # risk %
    rr: float               # reward : risk
    breakeven_win: float    # win rate (%) needed to break even (incl. fees)
    est_win: float          # estimated win rate (%) from conviction
    edge: float             # est_win - breakeven_win (positive = favorable)
    ev_pct: float           # expected value % per trade, net of fees
    favorable: bool
    basis: str              # human-readable explanation of the inputs

    def as_dict(self) -> dict:
        return {
            "target_pct": self.target_pct, "stop_pct": self.stop_pct, "rr": self.rr,
            "breakeven_win": self.breakeven_win, "est_win": self.est_win,
            "edge": self.edge, "ev_pct": self.ev_pct, "favorable": self.favorable,
            "basis": self.basis,
        }


def est_win_from_conviction(conviction: float) -> float:
    """Map the brain confidence (0-100) to an estimated win rate (%).

    Transparent, deliberately CONSERVATIVE heuristic (a confidence score is not a probability):
    30% floor + 0.32 per point → 50 conviction ≈ 46%, 75 ≈ 54%, 100 ≈ 62%. Caps at 70%.
    """
    c = max(0.0, min(100.0, conviction))
    return round(min(30.0 + c * 0.32, 70.0), 1)


def project(*, target_pct: float, stop_pct: float, conviction: float,
            atr_pct: float | None = None, trailing_trigger_pct: float = 0.0,
            fee_pct: float = 0.5, default_target_pct: float = 3.0) -> TradeProjection:
    """Project the EV of an entry. ``target_pct``/``stop_pct`` are the strategy's TP/SL (%);
    when ``target_pct`` is 0 (a trailing strategy) the reachable move is estimated from ATR
    (~2×ATR%), else the trailing trigger, else ``default_target_pct``. ``conviction`` is the
    brain confidence (0-100)."""
    basis: list[str] = []
    tgt = target_pct
    if tgt and tgt > 0:
        basis.append(f"TP {tgt:.1f}%")
    elif atr_pct and atr_pct > 0:
        tgt = round(2.0 * atr_pct, 2)
        basis.append(f"ATR×2 ~{tgt:.1f}%")
    elif trailing_trigger_pct and trailing_trigger_pct > 0:
        tgt = trailing_trigger_pct
        basis.append(f"trail trigger {tgt:.1f}%")
    else:
        tgt = default_target_pct
        basis.append(f"default {tgt:.1f}%")

    # No fixed SL (e.g. DCA, stop=0) → use a nominal risk for the math (the global breaker covers it).
    risk = stop_pct if stop_pct and stop_pct > 0 else default_target_pct
    denom = tgt + risk
    rr = round(tgt / risk, 2) if risk > 0 else 0.0
    # p*tgt - (1-p)*risk - fee = 0  →  p = (risk + fee) / (tgt + risk)
    breakeven = round((risk + fee_pct) / denom * 100.0, 1) if denom > 0 else 100.0
    est = est_win_from_conviction(conviction)
    p = est / 100.0
    ev = round(p * tgt - (1.0 - p) * risk - fee_pct, 3)
    basis.append(f"need {breakeven:.0f}% win, est {est:.0f}%")
    return TradeProjection(
        target_pct=round(tgt, 2), stop_pct=round(stop_pct, 2), rr=rr,
        breakeven_win=breakeven, est_win=est, edge=round(est - breakeven, 1),
        ev_pct=ev, favorable=ev > 0, basis=" · ".join(basis),
    )
