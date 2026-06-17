"""Confluence engine - turns raw indicators into a trader-style decision.

A serious discretionary trader doesn't obey one indicator; they weigh *confluence*
across pillars (trend, momentum, mean-reversion, volume, structure) **and shift what
they trust with the regime**: in a trend they follow momentum/structure pullbacks; in
chop they fade extremes. This module encodes exactly that, deterministically.

`evaluate_confluence(indicators)` returns a :class:`Confluence` with:
  - a decision (ENTER / WAIT / AVOID) and a 0–100 long-conviction score,
  - the per-signal votes and a human-readable checklist (sealed on-chain, shown live),
  - hard vetoes (e.g. never chase a vertical pump candle).

The action is derived **by code**; the LLM only confirms the narrative.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── tunables ─────────────────────────────────────────────────────────────────
ENTER_SCORE = 60.0          # long-conviction (0–100) needed to enter
MIN_POSITIVE_PILLARS = 3    # how many of the 5 pillars must lean long
TREND_ADX = 25.0            # ADX ≥ → trending; < RANGE_ADX → ranging
RANGE_ADX = 20.0

# per-regime pillar weights - what a trader pays attention to in each context
PILLAR_W: dict[str, dict[str, float]] = {
    "TREND":      {"trend": 1.5, "momentum": 1.2, "structure": 1.2, "meanrev": 0.5, "volume": 1.0},
    "RANGE":      {"trend": 0.5, "momentum": 1.0, "structure": 1.2, "meanrev": 1.5, "volume": 1.0},
    "TRANSITION": {"trend": 1.0, "momentum": 1.0, "structure": 1.1, "meanrev": 1.0, "volume": 1.0},
}
# macro regime makes the bar more selective when the market is risk-off
MACRO_THRESHOLD_BUMP = {"RISK_OFF": 18.0, "DEFENSIVE": 8.0, "NEUTRAL": 0.0, "BULL": -4.0}


@dataclass
class Signal:
    pillar: str        # trend | momentum | meanrev | volume | structure
    name: str
    vote: float        # -1..1, positive supports a LONG entry
    reason: str


@dataclass
class Confluence:
    decision: str                       # ENTER | WAIT | AVOID
    score: float                        # 0..100 long conviction
    mode: str                           # TREND | RANGE | TRANSITION
    signals: list[Signal] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    veto: str | None = None
    summary: str = ""

    @property
    def enter(self) -> bool:
        return self.decision == "ENTER"


def _micro_mode(ind: dict) -> str:
    adx = (ind.get("adx") or {}).get("adx")
    if adx is None:
        return "TRANSITION"
    if adx >= TREND_ADX:
        return "TREND"
    if adx < RANGE_ADX:
        return "RANGE"
    return "TRANSITION"


def _trend_signals(ind: dict) -> list[Signal]:
    out: list[Signal] = []
    ec = ind.get("ema_cross")
    if ec:
        v = 0.6 if ec["bull"] else -0.6
        out.append(Signal("trend", "ema", v,
                          f"EMA 9{'>' if ec['bull'] else '<'}21 ({'up' if ec['bull'] else 'down'}trend)"))
    a = ind.get("adx") or {}
    adx, pdi, mdi = a.get("adx"), a.get("plus_di"), a.get("minus_di")
    if adx is not None:
        if adx >= TREND_ADX and pdi is not None and mdi is not None:
            up = pdi > mdi
            out.append(Signal("trend", "adx", 0.7 if up else -0.7,
                              f"ADX {adx:.0f} strong {'up' if up else 'down'}trend"))
        elif adx < RANGE_ADX:
            out.append(Signal("trend", "adx", 0.0, f"ADX {adx:.0f} - no trend (chop)"))
    slope = ind.get("slope_pct")
    if slope is not None:
        if slope > 0.0005:
            out.append(Signal("trend", "slope", 0.4, "price slope rising"))
        elif slope < -0.0005:
            out.append(Signal("trend", "slope", -0.4, "price slope falling"))
    return out


def _momentum_signals(ind: dict, mode: str) -> list[Signal]:
    out: list[Signal] = []
    r = ind.get("rsi")
    if r is not None:
        if r < 30:
            out.append(Signal("momentum", "rsi", 0.8, f"RSI {r:.0f} oversold"))
        elif r > 70:
            out.append(Signal("momentum", "rsi", -0.7, f"RSI {r:.0f} overbought"))
        elif mode == "TREND" and 50 <= r <= 68:
            out.append(Signal("momentum", "rsi", 0.4, f"RSI {r:.0f} bullish momentum"))
    m = ind.get("macd")
    if m is not None:
        up = m["hist"] > 0
        out.append(Signal("momentum", "macd", 0.6 if up else -0.6,
                          f"MACD momentum {'up' if up else 'down'}"))
    return out


def _meanrev_signals(ind: dict) -> list[Signal]:
    out: list[Signal] = []
    b = ind.get("bollinger")
    if b is not None:
        if b["pct_b"] < 0.1:
            out.append(Signal("meanrev", "bbands", 0.8, "price at the lower Bollinger band"))
        elif b["pct_b"] > 0.9:
            out.append(Signal("meanrev", "bbands", -0.6, "price at the upper band (stretched)"))
    z = ind.get("zscore")
    if z is not None:
        if z < -1.5:
            out.append(Signal("meanrev", "zscore", 0.6, f"z {z:.1f} - cheap vs mean"))
        elif z > 2.0:
            out.append(Signal("meanrev", "zscore", -0.5, f"z {z:.1f} - over-extended"))
    return out


def _volume_signals(ind: dict) -> list[Signal]:
    out: list[Signal] = []
    vol = ind.get("volume")
    if vol and vol.get("surge"):
        out.append(Signal("volume", "surge", 0.4, f"volume surge {vol['ratio']:.1f}x"))
    vd = ind.get("vwap_dist_pct")
    if vd is not None:
        if vd > 0:
            out.append(Signal("volume", "vwap", 0.3, "above VWAP (intraday bid)"))
        else:
            out.append(Signal("volume", "vwap", -0.2, "below VWAP"))
    return out


def _structure_signals(ind: dict) -> tuple[list[Signal], str | None]:
    """Fibonacci structure - also returns a hard veto when chasing a pump."""
    out: list[Signal] = []
    veto: str | None = None
    f = ind.get("fibonacci") or {}
    pos = f.get("position")
    rat = f.get("rationale", "")
    if pos == "GOLDEN_POCKET":
        out.append(Signal("structure", "fib", 1.0, rat or "0.618 golden-pocket bounce"))
    elif pos == "FIB_SUPPORT":
        out.append(Signal("structure", "fib", 0.6, rat or "bounce at fib support"))
    elif pos == "BREAKDOWN":
        out.append(Signal("structure", "fib", -0.8, rat or "fib support failed"))
    elif pos == "NO_RETRACE":
        out.append(Signal("structure", "fib", 0.0, "no pullback yet - wait for a retrace"))
    elif pos == "CHASING_PUMP":
        out.append(Signal("structure", "fib", -1.0, rat or "vertical pump candle"))
        veto = rat or "chasing a vertical pump candle"
    return out, veto


def evaluate_confluence(ind: dict, macro_regime: str = "NEUTRAL") -> Confluence:
    """Combine the indicator readings into a deterministic long-entry decision."""
    mode = _micro_mode(ind)
    weights = PILLAR_W[mode]

    signals = _trend_signals(ind)
    signals += _momentum_signals(ind, mode)
    signals += _meanrev_signals(ind)
    signals += _volume_signals(ind)
    struct, veto = _structure_signals(ind)
    signals += struct

    # weighted long-conviction score (centre 50 = neutral)
    num = sum(s.vote * weights[s.pillar] for s in signals)
    den = sum(weights[s.pillar] for s in signals)
    wavg = (num / den) if den else 0.0
    score = round((wavg + 1) / 2 * 100, 1)

    # how many pillars net-positive
    pillar_net: dict[str, float] = {}
    for s in signals:
        pillar_net[s.pillar] = pillar_net.get(s.pillar, 0.0) + s.vote
    positive_pillars = sum(1 for v in pillar_net.values() if v > 0)

    threshold = ENTER_SCORE + MACRO_THRESHOLD_BUMP.get(macro_regime, 0.0)

    if veto:
        decision = "AVOID"
    elif score >= threshold and positive_pillars >= MIN_POSITIVE_PILLARS:
        decision = "ENTER"
    else:
        decision = "WAIT"

    reasons = [s.reason for s in sorted(signals, key=lambda s: -s.vote) if s.vote > 0]
    if veto:
        reasons = [f"VETO: {veto}"]
    summary = (f"{mode} · confluence {score:.0f}/100 · {positive_pillars}/5 pillars long "
               f"→ {decision}" + (f" - {reasons[0]}" if reasons else ""))

    return Confluence(decision=decision, score=score, mode=mode, signals=signals,
                      reasons=reasons, veto=veto, summary=summary)
