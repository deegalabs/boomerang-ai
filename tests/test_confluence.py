"""Confluence engine — decision logic across regimes, with vetoes."""
from __future__ import annotations

from boomerang.strategy.confluence import evaluate_confluence


def _ind(**over) -> dict:
    base = {
        "adx": {"adx": 30, "plus_di": 28, "minus_di": 12},
        "ema_cross": {"bull": True, "spread_pct": 0.01},
        "slope_pct": 0.001,
        "rsi": 55,
        "macd": {"hist": 0.5, "macd": 1.0, "signal": 0.5},
        "bollinger": {"pct_b": 0.4, "bandwidth": 0.05},
        "zscore": 0.2,
        "volume": {"surge": True, "ratio": 2.0},
        "vwap_dist_pct": 0.2,
        "fibonacci": {"position": "NEUTRAL", "rationale": "between levels"},
    }
    base.update(over)
    return base


def test_enter_trend_golden_pocket():
    c = evaluate_confluence(_ind(fibonacci={"position": "GOLDEN_POCKET",
                                            "rationale": "0.618 golden-pocket bounce — prime entry"}))
    assert c.decision == "ENTER"
    assert c.mode == "TREND"
    assert c.score >= 70
    assert c.enter is True
    assert any("golden" in r.lower() for r in c.reasons)


def test_avoid_chasing_pump_veto():
    c = evaluate_confluence(_ind(rsi=75, fibonacci={"position": "CHASING_PUMP",
                                                    "rationale": "vertical green candle (+7.0%) — don't chase"}))
    assert c.decision == "AVOID"
    assert c.veto is not None
    assert c.reasons and c.reasons[0].startswith("VETO")


def test_wait_when_chop_and_few_pillars():
    c = evaluate_confluence(_ind(
        adx={"adx": 15, "plus_di": 16, "minus_di": 15},
        slope_pct=0.0001, rsi=52, macd={"hist": 0.1, "macd": 0.1, "signal": 0.0},
        zscore=0.1, volume={"surge": False, "ratio": 0.8}, vwap_dist_pct=-0.1,
        fibonacci={"position": "NEUTRAL", "rationale": "between levels"}))
    assert c.decision == "WAIT"
    assert c.mode == "RANGE"


def test_enter_oversold_range_mean_reversion():
    c = evaluate_confluence(_ind(
        adx={"adx": 14, "plus_di": 12, "minus_di": 15},
        ema_cross={"bull": False, "spread_pct": -0.01}, slope_pct=-0.0002,
        rsi=25, macd={"hist": -0.1, "macd": -0.1, "signal": 0.0},
        bollinger={"pct_b": 0.05, "bandwidth": 0.08}, zscore=-1.8,
        volume={"surge": True, "ratio": 1.8}, vwap_dist_pct=-0.3,
        fibonacci={"position": "FIB_SUPPORT", "rationale": "bounce at the 0.5 support"}))
    assert c.decision == "ENTER"
    assert c.mode == "RANGE"
    assert any("oversold" in r.lower() for r in c.reasons)


def test_macro_regime_raises_the_bar():
    ind = _ind(
        adx={"adx": 14, "plus_di": 12, "minus_di": 15},
        ema_cross={"bull": False, "spread_pct": -0.01}, slope_pct=-0.0002,
        rsi=25, macd={"hist": -0.1, "macd": -0.1, "signal": 0.0},
        bollinger={"pct_b": 0.05, "bandwidth": 0.08}, zscore=-1.8,
        volume={"surge": True, "ratio": 1.8}, vwap_dist_pct=-0.3,
        fibonacci={"position": "FIB_SUPPORT", "rationale": "bounce at the 0.5 support"})
    assert evaluate_confluence(ind, macro_regime="NEUTRAL").decision == "ENTER"
    # same setup, but a risk-off market demands more conviction → holds back
    assert evaluate_confluence(ind, macro_regime="DEFENSIVE").decision == "WAIT"


def test_summary_is_human_readable():
    c = evaluate_confluence(_ind(fibonacci={"position": "GOLDEN_POCKET", "rationale": "0.618 bounce"}))
    assert "confluence" in c.summary and "ENTER" in c.summary and "TREND" in c.summary
