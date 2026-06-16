"""Fee-aware entry math (pure, unit-tested).

A real round-trip quote (buy then resell via the aggregator) already captures pool fees +
slippage + hidden tax as the ``retention`` ratio. Gas isn't in the quote, so we add it as a
percentage of the trade. The fee-aware gate rejects a **fee-dead** entry — one whose total
round-trip cost leaves no edge for the strategy's target — protecting small-capital PnL.
"""
from __future__ import annotations


def roundtrip_cost_pct(retention: float, amount_usd: float, gas_roundtrip_usd: float) -> float:
    """Total round-trip cost in % = (fees+slippage+tax from the quote) + gas as % of size."""
    quote_cost = (1.0 - retention) * 100.0
    gas_pct = (gas_roundtrip_usd / amount_usd * 100.0) if amount_usd > 0 else 0.0
    return quote_cost + gas_pct


def is_fee_dead(retention: float, amount_usd: float, gas_roundtrip_usd: float,
                min_edge_pct: float) -> bool:
    """True if the round-trip cost meets/exceeds the minimum edge → skip the entry."""
    return roundtrip_cost_pct(retention, amount_usd, gas_roundtrip_usd) >= min_edge_pct
