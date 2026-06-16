"""Technical-analysis indicators — deterministic unit tests."""
from __future__ import annotations

import pytest

from boomerang.strategy.indicators import (
    Kline, adx, atr, bollinger, compute_indicators, ema_cross, fibonacci,
    linreg_slope, macd, obv, rsi, volume_surge, vwap, zscore)


def _k(rows: list[tuple]) -> list[Kline]:
    """rows = (open, high, low, close, volume)."""
    return [Kline(*r) for r in rows]


def _flat(closes: list[float], spread: float = 1.0, vol: float = 100.0) -> list[Kline]:
    """Build candles from a close series (high/low = close ± spread/2)."""
    out = []
    prev = closes[0]
    for c in closes:
        out.append(Kline(open=prev, high=max(c, prev) + spread / 2,
                         low=min(c, prev) - spread / 2, close=c, volume=vol))
        prev = c
    return out


# ── trend ────────────────────────────────────────────────────────────────────
def test_ema_cross_direction():
    up = list(range(1, 40))
    assert ema_cross(up)["bull"] is True
    assert ema_cross(up[::-1])["bull"] is False
    assert ema_cross([1.0, 2.0]) is None          # not enough data


def test_linreg_slope_sign():
    assert linreg_slope(list(range(1, 30))) > 0   # rising
    assert linreg_slope(list(range(30, 1, -1))) < 0


def test_adx_flags_trend_and_direction():
    up = adx(_flat([100 + 2 * i for i in range(40)]))
    assert up["adx"] > 20 and up["plus_di"] > up["minus_di"]
    down = adx(_flat([100 - 2 * i for i in range(40)]))
    assert down["minus_di"] > down["plus_di"]


# ── momentum ─────────────────────────────────────────────────────────────────
def test_rsi_extremes():
    assert rsi(list(range(1, 30))) == 100.0       # only gains
    assert rsi(list(range(30, 1, -1))) == 0.0     # only losses
    assert rsi([1.0, 2.0]) is None


def test_macd_follows_trend():
    assert macd(list(range(1, 60)))["macd"] > 0
    assert macd(list(range(60, 1, -1)))["macd"] < 0


# ── volatility / mean-reversion ──────────────────────────────────────────────
def test_bollinger_constant_and_ascending():
    flat = bollinger([100.0] * 25)
    assert flat["bandwidth"] == 0.0 and flat["pct_b"] == 0.5
    asc = bollinger(list(map(float, range(1, 40))))
    assert asc["pct_b"] > 0.85                    # last point hugs the upper band


def test_zscore():
    assert zscore([5.0] * 20) == 0.0
    assert zscore(list(map(float, range(1, 21)))) > 1.0


def test_atr_constant_range():
    candles = [Kline(100, 101, 99, 100, 1) for _ in range(20)]
    assert atr(candles) == pytest.approx(2.0, abs=1e-6)


# ── volume ───────────────────────────────────────────────────────────────────
def test_vwap_weighted():
    candles = _k([(100, 100, 100, 100, 1), (110, 110, 110, 110, 3)])
    assert vwap(candles) == pytest.approx(107.5)


def test_obv_accumulates_up_moves():
    assert obv(_flat([float(i) for i in range(1, 11)], vol=10)) == pytest.approx(90.0)


def test_volume_surge():
    base = [Kline(1, 1, 1, 1, 1.0) for _ in range(11)]
    spike = base + [Kline(1, 1, 1, 1, 10.0)]
    assert volume_surge(spike)["surge"] is True
    assert volume_surge(base + [Kline(1, 1, 1, 1, 1.0)])["surge"] is False


# ── structure: Fibonacci ─────────────────────────────────────────────────────
def test_fibonacci_golden_pocket():
    data = _k([
        (100, 101, 100, 101, 100),
        (101, 103, 100, 103, 100),
        (103, 106, 103, 106, 100),
        (106, 110, 106, 110, 100),   # swing high
        (110, 110, 104, 105, 100),   # pullback
        (105, 105, 103.5, 104, 100),
        (104, 104, 103.6, 103.9, 100),
        (103.9, 105, 103.8, 104.8, 100),  # green bounce off 0.618 (~103.82)
    ])
    f = fibonacci(data, drop_last=False)
    assert f["position"] == "GOLDEN_POCKET" and f["golden_pocket"] is True


def test_fibonacci_chasing_pump():
    data = _k([
        (100, 101, 99, 100, 100), (100, 102, 100, 101, 100),
        (101, 103, 101, 102, 100), (102, 104, 102, 103, 100),
        (103, 105, 103, 104, 100), (104, 106, 104, 105, 100),
        (105, 107, 105, 106, 100), (106, 108, 106, 107, 100),
        (107, 116, 107, 114.5, 100),  # +7% vertical candle
    ])
    assert fibonacci(data, drop_last=False)["position"] == "CHASING_PUMP"


def test_fibonacci_no_retrace():
    data = _k([
        (98, 99, 98, 99, 100), (99, 100, 99, 100, 100),
        (100, 101, 100, 101, 100), (101, 103, 100, 103, 100),
        (103, 106, 103, 106, 100), (106, 110, 106, 110, 100),  # swing high
        (110, 110, 108, 109, 100), (109, 110, 108.5, 109.5, 100),  # still at highs
    ])
    assert fibonacci(data, drop_last=False)["position"] == "NO_RETRACE"


def test_fibonacci_needs_data():
    assert fibonacci(_flat([1.0, 2.0, 3.0]))["valid"] is False


# ── summary ──────────────────────────────────────────────────────────────────
def test_compute_indicators_shape():
    out = compute_indicators(_flat([100 + i * 0.5 for i in range(60)]))
    for key in ("price", "rsi", "macd", "ema_cross", "adx", "bollinger",
                "zscore", "atr", "vwap", "volume", "fibonacci", "slope_pct"):
        assert key in out
    assert out["price"] == pytest.approx(129.5)
