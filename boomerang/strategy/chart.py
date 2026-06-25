"""Chart-structure reading — the skill a real trader uses beyond raw indicators.

From candles it derives WHERE price sits relative to STRUCTURE: support/resistance
levels (from swing pivots), the market structure (uptrend / downtrend / range via
higher-highs-lows), the position within the support→resistance band, and the natural
risk:reward of a long taken here (target = resistance, stop = below support).

Deterministic and pure — fed to the brain so it reasons like a trader ("buying near
support with resistance above = good R:R; don't buy into resistance; respect the trend")
and used to build STRUCTURE-BASED stops/targets instead of fixed percentages.
"""
from __future__ import annotations

from dataclasses import dataclass

from boomerang.strategy.indicators import Kline


@dataclass(frozen=True)
class ChartStructure:
    price: float
    trend: str                          # "up" | "down" | "range"
    support: float | None               # nearest level below price
    resistance: float | None            # nearest level above price
    support_dist_pct: float | None      # how far below price the support sits
    resistance_dist_pct: float | None   # how far above price the resistance sits
    position: str                       # "near_support" | "near_resistance" | "mid_range"
    rr_to_resistance: float | None       # natural long R:R entering here (reward/risk)
    levels: tuple                        # all key levels (sorted)

    def as_dict(self) -> dict:
        return {
            "price": round(self.price, 8),
            "trend": self.trend,
            "support": round(self.support, 8) if self.support else None,
            "resistance": round(self.resistance, 8) if self.resistance else None,
            "support_dist_pct": self.support_dist_pct,
            "resistance_dist_pct": self.resistance_dist_pct,
            "position": self.position,
            "rr_to_resistance": self.rr_to_resistance,
        }


def _pivots(values: list[float], window: int, kind: str) -> list[float]:
    """Local extrema: a swing high is higher than `window` candles on each side (low: lower)."""
    out: list[float] = []
    n = len(values)
    for i in range(window, n - window):
        seg = values[i - window:i + window + 1]
        v = values[i]
        if kind == "high" and v >= max(seg):
            out.append(v)
        elif kind == "low" and v <= min(seg):
            out.append(v)
    return out


def _cluster(levels: list[float], tol_pct: float) -> list[float]:
    """Merge levels within tol_pct of each other into a single (averaged) level."""
    if not levels:
        return []
    levels = sorted(levels)
    out: list[float] = []
    grp = [levels[0]]
    for v in levels[1:]:
        if abs(v - grp[-1]) / grp[-1] * 100.0 <= tol_pct:
            grp.append(v)
        else:
            out.append(sum(grp) / len(grp))
            grp = [v]
    out.append(sum(grp) / len(grp))
    return out


def analyze_structure(klines: list[Kline], *, window: int = 3,
                      tol_pct: float = 0.6) -> ChartStructure | None:
    """Read the chart's structure + levels. None if there aren't enough candles."""
    if not klines or len(klines) < 2 * window + 5:
        return None
    highs = [k.high for k in klines]
    lows = [k.low for k in klines]
    price = klines[-1].close
    if price <= 0:
        return None

    swing_highs = _pivots(highs, window, "high")
    swing_lows = _pivots(lows, window, "low")
    res_levels = _cluster(swing_highs, tol_pct)
    sup_levels = _cluster(swing_lows, tol_pct)

    # nearest support below / resistance above; fall back to the window's extremes
    sup = max((lvl for lvl in sup_levels if lvl < price), default=None) or min(lows)
    res = min((lvl for lvl in res_levels if lvl > price), default=None) or max(highs)

    sup_dist = round((price - sup) / price * 100.0, 2) if sup and sup < price else None
    res_dist = round((res - price) / price * 100.0, 2) if res and res > price else None

    # market structure from the swing sequence (HH/HL = up, LH/LL = down)
    trend = "range"
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh, hl = swing_highs[-1] > swing_highs[-2], swing_lows[-1] > swing_lows[-2]
        lh, ll = swing_highs[-1] < swing_highs[-2], swing_lows[-1] < swing_lows[-2]
        if hh and hl:
            trend = "up"
        elif lh and ll:
            trend = "down"

    # position within the support→resistance band
    position = "mid_range"
    if res and sup and res > sup:
        frac = (price - sup) / (res - sup)
        if frac <= 0.25:
            position = "near_support"
        elif frac >= 0.75:
            position = "near_resistance"

    # natural long risk:reward (target=resistance, risk=distance to support)
    rr = None
    if sup and res and price > sup and res > price:
        risk, reward = price - sup, res - price
        rr = round(reward / risk, 2) if risk > 0 else None

    levels = tuple(round(lvl, 8) for lvl in sorted(set(sup_levels + res_levels)))
    return ChartStructure(price=price, trend=trend, support=sup, resistance=res,
                          support_dist_pct=sup_dist, resistance_dist_pct=res_dist,
                          position=position, rr_to_resistance=rr, levels=levels)
