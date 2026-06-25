"""Chart-structure reading: support/resistance, market structure, natural R:R."""
from __future__ import annotations

from boomerang.strategy.chart import analyze_structure
from boomerang.strategy.indicators import Kline


def _k(c: float, spread: float = 0.4) -> Kline:
    return Kline(open=c, high=c + spread, low=c - spread, close=c, volume=1.0)


def _series(closes: list[float]) -> list[Kline]:
    return [_k(c) for c in closes]


def _rising() -> list[Kline]:
    # rising zigzag: higher highs AND higher lows -> uptrend
    closes: list[float] = []
    for cyc in range(4):
        low = 100 + cyc * 3
        closes += [low, low + 2, low + 4, low + 2]  # trough -> peak -> pullback
    return _series(closes)


def test_too_few_candles_returns_none():
    assert analyze_structure(_series([1, 2, 3, 4])) is None


def test_levels_bracket_price():
    s = analyze_structure(_rising(), window=2)
    assert s is not None
    assert s.support is not None and s.resistance is not None
    assert s.support <= s.price <= s.resistance


def test_rr_positive_between_levels():
    s = analyze_structure(_rising(), window=2)
    assert s.rr_to_resistance is None or s.rr_to_resistance > 0


def test_uptrend_detected():
    s = analyze_structure(_rising(), window=2)
    assert s.trend == "up"


def test_as_dict_has_keys():
    s = analyze_structure(_rising(), window=2)
    d = s.as_dict()
    for k in ("price", "trend", "support", "resistance", "position", "rr_to_resistance"):
        assert k in d
