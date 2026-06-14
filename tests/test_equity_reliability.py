"""Anti false-circuit-breaker: an equity reading missing a held position is unreliable."""
from __future__ import annotations

from boomerang.risk.risk_engine import equity_reading_reliable


def test_reliable_when_all_positions_priced():
    holdings = [{"symbol": "USDC", "value_usd": 2.0}, {"symbol": "TWT", "value_usd": 4.0}]
    assert equity_reading_reliable(holdings, ["TWT"]) is True


def test_unreliable_when_a_position_is_missing():
    holdings = [{"symbol": "USDC", "value_usd": 2.0}]   # TWT not priced (RPC/route glitch)
    assert equity_reading_reliable(holdings, ["TWT"]) is False


def test_unreliable_when_a_position_priced_at_zero():
    holdings = [{"symbol": "TWT", "value_usd": 0.0}]
    assert equity_reading_reliable(holdings, ["TWT"]) is False


def test_reliable_with_no_open_positions():
    assert equity_reading_reliable([{"symbol": "USDC", "value_usd": 6.0}], []) is True


def test_case_insensitive_match():
    holdings = [{"symbol": "twt", "value_usd": 4.0}]
    assert equity_reading_reliable(holdings, ["TWT"]) is True
