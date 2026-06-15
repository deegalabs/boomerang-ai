"""MULTI-STRATEGY playbook routed by the market REGIME.

Three strategies, each with a DETERMINISTIC entry trigger and its own exit
management. The selector picks the active strategy by the regime (fear/greed)
and by the token's signals; the brain (Opus) CONFIRMS the setup (go/no-go + conviction).
The global locks (drawdown circuit breaker, daily cap, depeg, liquidity/oracle)
still apply ON TOP OF any strategy.

  1) MOMENTUM (uptrend): catches young thrust with rising volume; cuts the
     loss very short and lets the winner run (trailing).
  2) MEAN-REVERSION (sideways/chop): buys a short DIP of a token strong on the day,
     betting on the return to the mean; fixed, surgical TP and SL.
  3) DCA (panic/extreme fear): buys a solid asset in free fall aiming at a bounce;
     no fixed SL (covered by the global circuit breaker + 24h time-stop).

NOTE (volume proxy): CMC on our plan does not expose HOURLY volume, so the
"vol 1h >= 15% of 24h" filter of the momentum strategy is approximated by
volume_change_24h_pct (surge of interest on the day). It is a proxy for "rising
interest", not the exact 1h.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Trigger thresholds (proxies/tunable) ─────────────────────────────────────
VOL_ACCEL_MIN = 20.0    # momentum: volume_change_24h_pct >= 20% = rising interest (proxy of the 1h)
VOL_STABLE_MAX = 40.0   # mean-rev: volume must not be EXPLODING (range, no trend)
PANIC_FNG = 25          # F&G < 25 = extreme fear → panic regime (only DCA trades)
DCA_REBOUND_MIN_1H = 0.5  # Crisis Rebound: 1h must be > +0.5% (the bounce STARTED) before buying the panic


@dataclass(frozen=True)
class StrategySpec:
    """Definition of a strategy: identity + EXIT parameters.

    stop_pct=0 → NO fixed SL (depends on the global circuit breaker). take_profit_pct=0 →
    no fixed cap (lets it run via trailing). trailing_trigger_pct=0 → no trailing.
    time_stop_min=0 → uses the global time-stop from config. band 999 → time-stop by
    pure TIME (exits on schedule regardless of PnL)."""

    key: str
    label: str
    stop_pct: float
    take_profit_pct: float
    trailing_trigger_pct: float
    trailing_pct: float
    time_stop_min: float
    time_stop_band_pct: float


MOMENTUM = StrategySpec(
    "momentum", "Momentum/Attention",
    stop_pct=1.0, take_profit_pct=0.0,
    trailing_trigger_pct=2.5, trailing_pct=1.5,
    time_stop_min=20.0, time_stop_band_pct=0.2,
)

MEAN_REVERSION = StrategySpec(
    "mean_reversion", "Mean Reversion",
    stop_pct=0.8, take_profit_pct=1.2,
    trailing_trigger_pct=0.0, trailing_pct=0.0,
    time_stop_min=0.0, time_stop_band_pct=0.0,
)

DCA = StrategySpec(
    "dca", "DCA/Accumulation",
    stop_pct=0.0, take_profit_pct=3.0,                 # no fixed SL; TP +3% on the bounce
    trailing_trigger_pct=0.0, trailing_pct=0.0,
    time_stop_min=1440.0, time_stop_band_pct=999.0,    # 24h, by pure TIME (band 999 = any PnL)
)

ALL = (MOMENTUM, MEAN_REVERSION, DCA)
_BY_KEY = {s.key: s for s in ALL}


def by_key(key: str) -> StrategySpec | None:
    return _BY_KEY.get(key or "")


def select_strategy(fng: int | None, metrics: dict) -> StrategySpec | None:
    """Routes to the active strategy by the REGIME (fear/greed) + token signals.
    Returns the StrategySpec whose deterministic trigger FIRES, or None.

    The triggers are almost mutually exclusive (1h cannot be >+2.5% and <-2% at
    the same time). In PANIC, ONLY DCA trades (scalping would be stopped out by volatility)."""
    p1 = metrics.get("percent_change_1h")
    p24 = metrics.get("percent_change_24h")
    if p1 is None or p24 is None:
        return None
    vc = metrics.get("volume_change_24h_pct") or 0.0

    # PANIC (extreme fear): forbids scalping; only DCA on a solid asset in free fall.
    # CRISIS REBOUND: don't catch the falling knife — only buy once the bounce has STARTED
    # (price ticking up in the last hour while 24h is deeply negative).
    if fng is not None and fng < PANIC_FNG:
        if p24 < -10.0 and p1 > DCA_REBOUND_MIN_1H:
            return DCA
        return None

    # MOMENTUM: young thrust (strong 1h), aligned upward (24h>0), with rising volume.
    if p1 > 2.5 and p24 > 0.0 and vc >= VOL_ACCEL_MIN:
        return MOMENTUM

    # MEAN-REVERSION: short dip (1h<-2%) of a token STRONG on the day (24h>+4%), range
    # (stable volume, not exploding = no trend).
    if p1 < -2.0 and p24 > 4.0 and vc <= VOL_STABLE_MAX:
        return MEAN_REVERSION

    return None


def setup_strength(spec: StrategySpec, metrics: dict) -> float:
    """Setup strength to RANK the cycle's candidates (which to evaluate first in Opus)."""
    p1 = metrics.get("percent_change_1h") or 0.0
    p24 = metrics.get("percent_change_24h") or 0.0
    vc = metrics.get("volume_change_24h_pct") or 0.0
    if spec.key == "momentum":
        return 50.0 + min(p1, 10.0) * 2.0 + min(max(vc, 0.0), 100.0) * 0.3
    if spec.key == "mean_reversion":
        return 40.0 + min(p24, 20.0) + min(-p1, 10.0)
    return 30.0 + min(-p24, 40.0)  # dca: the deeper the drop, the greater the potential bounce


# ── ACTION MATRIX: the market regime dictates the posture (NEXUS-style) ───────
@dataclass(frozen=True)
class Posture:
    """What the current MARKET regime allows: which strategies, how big, how many.

    size_mult multiplies the conviction-sized position; max_positions caps the concurrent
    open positions; allowed is the set of strategy keys permitted to OPEN this cycle."""

    label: str
    size_mult: float
    max_positions: int
    allowed: frozenset


_RISK_TRADE = frozenset({"momentum", "mean_reversion"})


def regime_posture(fng: int | None, btc_24h: float | None, funding: float | None) -> Posture:
    """Maps the macro regime to a trading posture. The deterministic 'Action Matrix':
    in panic only DCA (smaller, fewer); in a BTC crash stand fully down; when defensive
    (BTC soft / extreme greed / overleveraged funding) shrink size and positions."""
    if fng is not None and fng < PANIC_FNG:
        return Posture("PANIC", 0.7, 2, frozenset({"dca"}))      # only DCA, modest size
    if btc_24h is not None and btc_24h <= -5.0:
        return Posture("RISK_OFF", 0.0, 0, frozenset())          # systemic crash → no entries
    overleveraged = funding is not None and funding >= 0.0005
    defensive = ((btc_24h is not None and btc_24h <= -2.0)
                 or (fng is not None and fng >= 78) or overleveraged)
    if defensive:
        return Posture("DEFENSIVE", 0.6, 2, _RISK_TRADE)         # smaller, fewer
    if btc_24h is not None and btc_24h >= 3.0:
        return Posture("BULL", 1.0, 3, _RISK_TRADE)              # full weight
    return Posture("NEUTRAL", 0.85, 3, _RISK_TRADE)


# ── STRATEGY ARBITER: deactivate a strategy with negative expectancy ──────────
def expectancy_disabled(closes: list, *, min_trades: int = 6, min_expectancy: float = -0.5) -> set:
    """Returns the strategy keys to DEACTIVATE because their recent EXPECTANCY (avg PnL%
    per trade) is clearly negative — even if the win-rate looks fine (NEXUS's lesson: a
    67%-WR strategy can still bleed). Needs >= min_trades closed trades per strategy to act;
    dormant until enough history accrues. Determines from the recorded {strategy, pnl_pct}."""
    from collections import defaultdict
    by_strat: dict[str, list] = defaultdict(list)
    for t in closes:
        key = (t.get("strategy") or "").strip()
        pnl = t.get("pnl_pct")
        if key and pnl is not None:
            by_strat[key].append(pnl)
    disabled = set()
    for key, pnls in by_strat.items():
        recent = pnls[-20:]
        if len(recent) >= min_trades and sum(recent) / len(recent) < min_expectancy:
            disabled.add(key)
    return disabled
