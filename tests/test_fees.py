"""Fee-aware entry gate — pure round-trip cost math."""
from __future__ import annotations

import pytest

from boomerang.vault.fees import is_fee_dead, roundtrip_cost_pct


def test_roundtrip_cost_combines_quote_and_gas():
    # retention 0.99 = 1% quote cost; $0.70 gas on $100 = 0.7% → 1.7%
    assert roundtrip_cost_pct(0.99, 100.0, 0.70) == pytest.approx(1.7, abs=1e-6)


def test_cheap_entry_is_not_fee_dead():
    # 0.5% quote + 0.2% gas = 0.7% < 1.5% edge → tradeable
    assert is_fee_dead(0.995, 100.0, 0.20, min_edge_pct=1.5) is False


def test_expensive_roundtrip_is_fee_dead():
    assert is_fee_dead(0.99, 100.0, 0.70, min_edge_pct=1.5) is True   # 1.7% ≥ 1.5%


def test_tiny_size_gas_dominates():
    # $0.70 round-trip gas on a $10 trade = 7% → fee-dead even with a clean quote
    assert is_fee_dead(0.997, 10.0, 0.70, min_edge_pct=1.5) is True


def test_zero_amount_no_div_error():
    assert roundtrip_cost_pct(0.99, 0.0, 0.70) == pytest.approx(1.0, abs=1e-6)
