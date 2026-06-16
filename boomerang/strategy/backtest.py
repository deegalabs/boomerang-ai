"""Lightweight, causal backtest harness for a StrategySpec + a TA trigger.

Replays historical 1m candles: at each bar it evaluates the trigger on the indicators
computed over the candles **up to that bar only** (no lookahead); on a fire it opens and
then manages the exit (SL / TP / trailing / time-stop) from the spec's parameters on the
following bars. Reports expectancy, win-rate and max drawdown so a new strategy can be
**validated before it is enabled live**. Pure (no network, no agent) and unit-tested —
fetch the candles with `strategy/klines.py` and pass them in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from boomerang.strategy.indicators import Kline, compute_indicators


@dataclass(frozen=True)
class BacktestResult:
    trades: int
    wins: int
    win_rate: float        # %
    expectancy: float      # avg net PnL % per trade
    total_pnl: float       # sum of net PnL %
    max_drawdown: float    # % of the equity curve (trade-by-trade)
    pnls: list             # per-trade net PnL %


def run_backtest(klines: list[Kline], spec, trigger: Callable[[dict], bool], *,
                 warmup: int = 30, fee_pct: float = 0.5) -> BacktestResult:
    """Backtest ``spec`` entered whenever ``trigger(indicators)`` fires. Bars are treated as
    minutes (1m candles) for the time-stop. ``fee_pct`` is the round-trip cost subtracted per
    trade. One position at a time (matches the live single-entry model per symbol)."""
    pnls: list[float] = []
    n = len(klines)
    i = warmup
    while i < n - 1:
        ind = compute_indicators(klines[: i + 1])
        if not trigger(ind):
            i += 1
            continue
        entry = klines[i].close
        if entry <= 0:
            i += 1
            continue
        stop = entry * (1 - spec.stop_pct / 100.0) if spec.stop_pct > 0 else 0.0
        tp = entry * (1 + spec.take_profit_pct / 100.0) if spec.take_profit_pct > 0 else 0.0
        peak = entry
        trail_on = False
        exit_price = klines[-1].close
        exit_j = n - 1
        for j in range(i + 1, n):
            bar = klines[j]
            peak = max(peak, bar.high)
            gain = (peak - entry) / entry * 100.0
            if not trail_on and spec.trailing_trigger_pct > 0 and gain >= spec.trailing_trigger_pct:
                trail_on = True
            if trail_on:
                trail_stop = peak * (1 - (spec.trailing_pct or spec.stop_pct) / 100.0)
                if bar.low <= trail_stop:
                    exit_price, exit_j = trail_stop, j
                    break
            if stop > 0 and bar.low <= stop:
                exit_price, exit_j = stop, j
                break
            if tp > 0 and bar.high >= tp:
                exit_price, exit_j = tp, j
                break
            if spec.time_stop_min > 0 and (j - i) >= spec.time_stop_min:
                exit_price, exit_j = bar.close, j
                break
        pnls.append((exit_price - entry) / entry * 100.0 - fee_pct)
        i = exit_j + 1  # no overlapping positions

    trades = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    # max drawdown of the cumulative equity curve
    cum = 0.0
    peak_eq = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak_eq = max(peak_eq, cum)
        max_dd = max(max_dd, peak_eq - cum)
    return BacktestResult(
        trades=trades, wins=wins,
        win_rate=round(wins / trades * 100, 1) if trades else 0.0,
        expectancy=round(total / trades, 3) if trades else 0.0,
        total_pnl=round(total, 2), max_drawdown=round(max_dd, 2), pnls=pnls,
    )
