"""Backtest harness + TA strategy selector."""
from __future__ import annotations

from boomerang.strategy.backtest import run_backtest
from boomerang.strategy.indicators import Kline
from boomerang.strategy.playbook import (
    TREND_FOLLOW, VOL_SQUEEZE, VWAP_REVERSION, ta_select)


def _series(closes, spread=0.6):
    out, prev = [], closes[0]
    for c in closes:
        out.append(Kline(open=prev, high=max(c, prev) + spread, low=min(c, prev) - spread, close=c, volume=100))
        prev = c
    return out


def test_backtest_rising_hits_take_profit():
    rising = _series([100 + i * 0.6 for i in range(40)])   # steady uptrend
    res = run_backtest(rising, VOL_SQUEEZE, lambda ind: True, warmup=5)
    assert res.trades >= 1
    assert res.expectancy > 0            # TP +3% net of fee → positive expectancy
    assert res.win_rate > 50


def test_backtest_falling_hits_stop():
    falling = _series([100 - i * 0.6 for i in range(40)])  # steady downtrend
    res = run_backtest(falling, VOL_SQUEEZE, lambda ind: True, warmup=5)
    assert res.trades >= 1
    assert res.expectancy < 0            # SL -1% + fee → negative expectancy


def test_backtest_no_trigger_no_trades():
    flat = _series([100.0] * 40)
    res = run_backtest(flat, VOL_SQUEEZE, lambda ind: False, warmup=5)
    assert res.trades == 0 and res.expectancy == 0.0


def test_ta_select_trend_follow():
    ind = {"ema_cross": {"bull": True}, "adx": {"adx": 31}, "vwap_dist_pct": 0.3}
    assert ta_select(ind) is TREND_FOLLOW


def test_ta_select_vol_squeeze():
    ind = {"ema_cross": {"bull": False}, "adx": {"adx": 18},
           "bollinger": {"bandwidth": 0.03, "pct_b": 0.85}, "volume": {"surge": True}}
    assert ta_select(ind) is VOL_SQUEEZE


def test_ta_select_vwap_reversion():
    ind = {"ema_cross": {"bull": True}, "adx": {"adx": 20}, "vwap_dist_pct": -0.5, "rsi": 55}
    assert ta_select(ind) is VWAP_REVERSION


def test_ta_select_none_when_flat():
    assert ta_select({"ema_cross": {"bull": False}, "adx": {"adx": 12}}) is None
