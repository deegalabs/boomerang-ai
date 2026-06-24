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
# Extreme fear locks down to DCA-only ONLY when the TAPE is actually falling. If sentiment is
# fearful but BTC is flat/up (no crash happening), the full lockdown idles the agent for nothing —
# so above this BTC/24h threshold we treat the regime as "fear but stable" and allow cautious risk.
FEAR_STABLE_BTC = -2.0


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
    # 5m-timeframe exits: a wide stop survives ordinary 5m noise (the 1% stop bled on noise),
    # the trailing only arms after a real move, and a longer time-stop lets the thrust develop.
    stop_pct=3.0, take_profit_pct=0.0,
    trailing_trigger_pct=4.0, trailing_pct=2.0,
    time_stop_min=60.0, time_stop_band_pct=0.3,
)

MEAN_REVERSION = StrategySpec(
    "mean_reversion", "Mean Reversion",
    # 5m-timeframe exits: stop wide enough to survive noise, TP at 2:1 R/R clears round-trip
    # friction (0.5% fees + slippage) with real margin; time-stop frees capital if it stalls.
    stop_pct=2.5, take_profit_pct=5.0,
    trailing_trigger_pct=0.0, trailing_pct=0.0,
    time_stop_min=180.0, time_stop_band_pct=0.6,
)

DCA = StrategySpec(
    "dca", "DCA/Accumulation",
    stop_pct=0.0, take_profit_pct=3.0,                 # no fixed SL; TP +3% on the bounce
    trailing_trigger_pct=0.0, trailing_pct=0.0,
    time_stop_min=1440.0, time_stop_band_pct=999.0,    # 24h, by pure TIME (band 999 = any PnL)
)

# ── TA strategies (selected by the confluence indicators, on 5m candles) ──────
# Off by default (config `enable_ta_strategies`). Parameters were DERIVED EMPIRICALLY with the
# backtest harness over 20 liquid tokens: on 5m candles a wide stop (4%) + an 8% target flip BOTH
# concepts to positive expectancy (~+0.13%/trade, ~45% win, 120-176 trades); on 1m the tight stops
# bled (ordinary noise stops you out before the move). VWAP Reversion was RETIRED — it never reached
# positive expectancy even tuned (its premise, a reliable bounce back to VWAP, doesn't hold in this
# universe). The confluence layer + expectancy arbiter still gate every entry.
TREND_FOLLOW = StrategySpec(
    "trend_follow", "Trend Follow",
    stop_pct=4.0, take_profit_pct=8.0,                 # 5m: wide stop survives noise, ~2:1 target
    trailing_trigger_pct=0.0, trailing_pct=0.0,
    time_stop_min=240.0, time_stop_band_pct=0.4,
)

VOL_SQUEEZE = StrategySpec(
    "vol_squeeze", "Volatility Squeeze",
    stop_pct=4.0, take_profit_pct=8.0,                 # 5m: breakout from tight bands → 2:1 target
    trailing_trigger_pct=0.0, trailing_pct=0.0,
    time_stop_min=240.0, time_stop_band_pct=0.4,
)

ALL = (MOMENTUM, MEAN_REVERSION, DCA, TREND_FOLLOW, VOL_SQUEEZE)
_BY_KEY = {s.key: s for s in ALL}


def ta_select(ind: dict) -> StrategySpec | None:
    """Pick a TA strategy from the confluence indicators (5m klines). Deterministic, pure.
    Returns the first matching spec by priority, or None. Used ADDITIVELY: when enabled,
    the agent refines an ENTER candidate's exit profile with the matching TA pattern."""
    ec = ind.get("ema_cross") or {}
    adx = (ind.get("adx") or {}).get("adx") or 0.0
    bb = ind.get("bollinger") or {}
    vd = ind.get("vwap_dist_pct")
    vol = ind.get("volume") or {}
    # Trend Follow: established uptrend (EMA bull + strong ADX), price above VWAP.
    if ec.get("bull") and adx >= 25 and (vd or 0.0) > 0:
        return TREND_FOLLOW
    # Volatility Squeeze: tight Bollinger bands breaking up on volume.
    if bb.get("bandwidth", 1.0) <= 0.04 and bb.get("pct_b", 0.0) >= 0.8 and vol.get("surge"):
        return VOL_SQUEEZE
    return None


def by_key(key: str) -> StrategySpec | None:
    return _BY_KEY.get(key or "")


def select_strategy(fng: int | None, metrics: dict,
                    btc_24h: float | None = None, loose: bool = False) -> StrategySpec | None:
    """Routes to the active strategy by the REGIME (fear/greed) + token signals.
    Returns the StrategySpec whose deterministic trigger FIRES, or None.

    The triggers are almost mutually exclusive (1h cannot be >+2.5% and <-2% at
    the same time). In PANIC with a FALLING tape, ONLY DCA trades (scalping would be
    stopped out by the volatility). If fear is high but the tape is stable (BTC flat/up),
    fall through to the normal momentum/mean-reversion triggers — the posture sizes it down.

    `loose` (TEMP live-activity switch): drops the trigger bars so the agent transacts on a
    flat tape. Lower entry quality — the brain cutoff is loosened in tandem (see _effective_cut)."""
    p1 = metrics.get("percent_change_1h")
    p24 = metrics.get("percent_change_24h")
    if p1 is None or p24 is None:
        return None
    vc = metrics.get("volume_change_24h_pct") or 0.0

    # PANIC (extreme fear) + FALLING tape: forbids scalping; only DCA on a solid asset in
    # free fall. CRISIS REBOUND: don't catch the falling knife — only buy once the bounce has
    # STARTED (price ticking up in the last hour while 24h is deeply negative). When fear is high
    # but the tape is stable (BTC > FEAR_STABLE_BTC), skip this branch and route normally.
    panic_tape = btc_24h is None or btc_24h <= FEAR_STABLE_BTC
    if fng is not None and fng < PANIC_FNG and panic_tape:
        if p24 < -10.0 and p1 > DCA_REBOUND_MIN_1H:
            return DCA
        return None

    # Trigger thresholds — loosened for live activity when `loose` is set.
    mom_1h, mom_24h, mom_vol = (0.5, -2.0, -30.0) if loose else (2.5, 0.0, VOL_ACCEL_MIN)
    mr_1h, mr_24h, mr_vol = (-0.5, -2.0, 120.0) if loose else (-2.0, 4.0, VOL_STABLE_MAX)

    # MOMENTUM: young thrust (strong 1h), aligned upward (24h>0), with rising volume.
    if p1 > mom_1h and p24 > mom_24h and vc >= mom_vol:
        return MOMENTUM

    # MEAN-REVERSION: short dip (1h<-2%) of a token STRONG on the day (24h>+4%), range
    # (stable volume, not exploding = no trend).
    if p1 < mr_1h and p24 > mr_24h and vc <= mr_vol:
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


_TA_KEYS = frozenset({"trend_follow", "vol_squeeze"})
_RISK_TRADE = frozenset({"momentum", "mean_reversion"}) | _TA_KEYS  # TA keys harmless until enabled


def regime_posture(fng: int | None, btc_24h: float | None, funding: float | None) -> Posture:
    """Maps the macro regime to a trading posture. The deterministic 'Action Matrix':
    in panic only DCA (smaller, fewer); in a BTC crash stand fully down; when defensive
    (BTC soft / extreme greed / overleveraged funding) shrink size and positions."""
    if fng is not None and fng < PANIC_FNG:
        # Full DCA-only lockdown ONLY when the tape is actually falling. Fear + a stable/up
        # tape (no crash) → allow risk trades, but at a reduced size (more cautious than DEFENSIVE).
        if btc_24h is None or btc_24h <= FEAR_STABLE_BTC:
            return Posture("PANIC", 0.7, 2, frozenset({"dca"}))  # only DCA, modest size
        return Posture("FEAR_STABLE", 0.5, 2, _RISK_TRADE)       # fear but tape stable → cautious risk
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
