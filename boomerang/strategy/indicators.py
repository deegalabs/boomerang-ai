"""Pure technical-analysis indicators over OHLCV candles.

A real trader never reads one indicator — they look for *confluence* across trend,
momentum, volatility, volume and structure. This module provides the building blocks
(deterministic, side-effect-free, unit-tested); the confluence engine that combines
them into a decision lives separately. Every function returns the LATEST reading (the
number a decision needs), or ``None`` when there isn't enough data.

Candles are :class:`Kline` namedtuples (oldest first). For live data the final candle
is usually still forming — callers that care (e.g. Fibonacci) drop it.
"""
from __future__ import annotations

from typing import NamedTuple


class Kline(NamedTuple):
    open: float
    high: float
    low: float
    close: float
    volume: float


# ── helpers ──────────────────────────────────────────────────────────────────
def _ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA aligned to ``values`` (None until it can be seeded by an SMA)."""
    if len(values) < period:
        return [None] * len(values)
    out: list[float | None] = [None] * (period - 1)
    prev = sum(values[:period]) / period
    out.append(prev)
    k = 2 / (period + 1)
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def _stddev(window: list[float], mean: float) -> float:
    return (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5


# ── trend ────────────────────────────────────────────────────────────────────
def ema_cross(closes: list[float], fast: int = 9, slow: int = 21) -> dict | None:
    ef, es = _ema_series(closes, fast), _ema_series(closes, slow)
    if ef[-1] is None or es[-1] is None:
        return None
    f, s = ef[-1], es[-1]
    return {"fast": f, "slow": s, "bull": f > s, "spread_pct": (f - s) / s if s else 0.0}


def linreg_slope(closes: list[float], period: int = 20) -> float | None:
    """Least-squares slope over the last ``period`` closes, normalized as %/bar."""
    if len(closes) < period:
        return None
    y = closes[-period:]
    xm = (period - 1) / 2
    ym = sum(y) / period
    num = sum((i - xm) * (y[i] - ym) for i in range(period))
    den = sum((i - xm) ** 2 for i in range(period))
    slope = num / den if den else 0.0
    return slope / ym if ym else 0.0


def adx(klines: list[Kline], period: int = 14) -> dict | None:
    """Wilder ADX + directional indicators. ADX>25 ≈ trending; <20 ≈ chop."""
    n = len(klines)
    if n < 2 * period + 1:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = klines[i].high - klines[i - 1].high
        dn = klines[i - 1].low - klines[i].low
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        h, lo, pc = klines[i].high, klines[i].low, klines[i - 1].close
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))

    def smooth(arr: list[float]) -> list[float]:
        s = sum(arr[:period])
        out = [s]
        for i in range(period, len(arr)):
            s = s - s / period + arr[i]
            out.append(s)
        return out

    sp, sm, st = smooth(plus_dm), smooth(minus_dm), smooth(trs)
    dx = []
    for p, m, t in zip(sp, sm, st):
        pdi = 100 * p / t if t else 0.0
        mdi = 100 * m / t if t else 0.0
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0)
    pdi_last = 100 * sp[-1] / st[-1] if st[-1] else 0.0
    mdi_last = 100 * sm[-1] / st[-1] if st[-1] else 0.0
    if len(dx) < period:
        return {"adx": None, "plus_di": pdi_last, "minus_di": mdi_last}
    a = sum(dx[:period]) / period
    for i in range(period, len(dx)):
        a = (a * (period - 1) + dx[i]) / period
    return {"adx": a, "plus_di": pdi_last, "minus_di": mdi_last}


# ── momentum ─────────────────────────────────────────────────────────────────
def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if len(closes) < slow + signal:
        return None
    ef, es = _ema_series(closes, fast), _ema_series(closes, slow)
    macd_vals = [a - b for a, b in zip(ef, es) if a is not None and b is not None]
    if len(macd_vals) < signal:
        return None
    sig = _ema_series(macd_vals, signal)
    m, s = macd_vals[-1], sig[-1]
    if s is None:
        return None
    return {"macd": m, "signal": s, "hist": m - s}


# ── volatility / mean-reversion ──────────────────────────────────────────────
def bollinger(closes: list[float], period: int = 20, mult: float = 2.0) -> dict | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    sd = _stddev(window, mid)
    upper, lower = mid + mult * sd, mid - mult * sd
    price = closes[-1]
    pct_b = (price - lower) / (upper - lower) if upper > lower else 0.5
    return {"mid": mid, "upper": upper, "lower": lower, "sd": sd,
            "pct_b": pct_b, "bandwidth": (upper - lower) / mid if mid else 0.0}


def zscore(closes: list[float], period: int = 20) -> float | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    sd = _stddev(window, mean)
    return (closes[-1] - mean) / sd if sd else 0.0


def atr(klines: list[Kline], period: int = 14) -> float | None:
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        h, lo, pc = klines[i].high, klines[i].low, klines[i - 1].close
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    a = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a


# ── volume / participation ───────────────────────────────────────────────────
def vwap(klines: list[Kline]) -> float | None:
    num = den = 0.0
    for k in klines:
        tp = (k.high + k.low + k.close) / 3
        num += tp * k.volume
        den += k.volume
    return num / den if den else None


def obv(klines: list[Kline]) -> float | None:
    if len(klines) < 2:
        return None
    o = 0.0
    for i in range(1, len(klines)):
        if klines[i].close > klines[i - 1].close:
            o += klines[i].volume
        elif klines[i].close < klines[i - 1].close:
            o -= klines[i].volume
    return o


def volume_surge(klines: list[Kline], lookback: int = 10, mult: float = 1.5) -> dict | None:
    if len(klines) < lookback + 1:
        return None
    vols = [k.volume for k in klines]
    base = vols[-(lookback + 1):-1]
    avg = sum(base) / len(base) if base else 0.0
    ratio = vols[-1] / avg if avg > 0 else 0.0
    return {"ratio": ratio, "surge": avg > 0 and vols[-1] > mult * avg, "avg": avg}


# ── structure: Fibonacci retracement ─────────────────────────────────────────
def fibonacci(klines: list[Kline], chase: float = 0.06, bounce_window: int = 3,
              drop_last: bool = True) -> dict:
    """Classify where price sits on the recent swing's Fibonacci retracement.

    Positions: CHASING_PUMP (don't enter), NO_RETRACE (wait), GOLDEN_POCKET (0.618
    bounce — prime entry), FIB_SUPPORT (0.382/0.5 bounce), BREAKDOWN (past 0.786),
    NEUTRAL. ``drop_last`` skips the still-forming live candle.
    """
    out: dict = {"valid": False, "position": "UNKNOWN", "retrace": 0.0,
                 "golden_pocket": False, "rationale": ""}
    data = klines[:-1] if (drop_last and len(klines) > 1) else klines
    if len(data) < 8:
        return out
    highs = [k.high for k in data]
    lows = [k.low for k in data]
    opens = [k.open for k in data]
    closes = [k.close for k in data]

    hi_idx = max(range(len(highs)), key=lambda i: highs[i])
    swing_high = highs[hi_idx]
    swing_low = min(lows[: hi_idx + 1] if hi_idx > 0 else lows)
    move = swing_high - swing_low
    if move <= 0 or swing_low <= 0 or move / swing_low < 0.03:
        out["rationale"] = "no meaningful swing (<3%)"
        return out

    price = closes[-1]
    retrace = (swing_high - price) / move
    levels = {lv: swing_high - move * lv for lv in (0.236, 0.382, 0.5, 0.618, 0.786)}
    last_chg = (closes[-1] - opens[-1]) / opens[-1] if opens[-1] > 0 else 0.0
    is_green = last_chg > 0

    recent_low = min(lows[-bounce_window:])
    bounce_level = None
    for lv in (0.618, 0.5, 0.382):  # deepest first — 0.618 is THE level
        lp = levels[lv]
        if recent_low <= lp * 1.02 and price > lp and is_green:
            bounce_level = lv
            break

    if last_chg > chase:
        position, rat = "CHASING_PUMP", f"vertical green candle ({last_chg * 100:+.1f}%) — don't chase"
    elif retrace < 0.15:
        position, rat = "NO_RETRACE", "still near the highs — wait for a pullback"
    elif retrace > 0.786:
        position, rat = "BREAKDOWN", "blew through all fib supports"
    elif bounce_level == 0.618:
        position, rat = "GOLDEN_POCKET", "0.618 golden-pocket bounce — prime entry"
    elif bounce_level in (0.5, 0.382):
        position, rat = "FIB_SUPPORT", f"bounce at the {bounce_level} support"
    else:
        position, rat = "NEUTRAL", "between levels — no clear signal"

    out.update({
        "valid": True, "position": position, "retrace": round(retrace, 3),
        "golden_pocket": position == "GOLDEN_POCKET", "bounce_level": bounce_level,
        "levels": {str(k): round(v, 10) for k, v in levels.items()},
        "swing_high": swing_high, "swing_low": swing_low,
        "last_candle_chg": round(last_chg, 4), "rationale": rat,
    })
    return out


# ── one-shot summary (feeds the confluence engine and the live panel) ────────
def compute_indicators(klines: list[Kline]) -> dict:
    """Latest reading of every indicator in one dict. Missing data → None."""
    closes = [k.close for k in klines]
    price = closes[-1] if closes else None
    a = atr(klines)
    return {
        "price": price,
        "ema_cross": ema_cross(closes),
        "slope_pct": linreg_slope(closes),
        "adx": adx(klines),
        "rsi": rsi(closes),
        "macd": macd(closes),
        "bollinger": bollinger(closes),
        "zscore": zscore(closes),
        "atr": a,
        "atr_pct": (a / price * 100) if (a and price) else None,
        "vwap": vwap(klines),
        "vwap_dist_pct": ((price - vwap(klines)) / vwap(klines) * 100)
        if (price and vwap(klines)) else None,
        "obv": obv(klines),
        "volume": volume_surge(klines),
        "fibonacci": fibonacci(klines),
    }
