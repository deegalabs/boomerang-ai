"""In-process cache of the REAL quotes the agent (Track 1) already fetches from CMC.

The agent thread writes here every cycle; the site (demo) thread reads — same
process, same module object. This way the demo operates on the REAL MARKET (real
prices and changes) WITHOUT any extra call to CoinMarketCap (zero cost).
"""
from __future__ import annotations

import time

_data: dict = {"quotes": {}, "btc_24h": None, "fng": None, "ts": 0.0}


def put(quotes: dict, btc_24h, fng=None) -> None:  # noqa: ANN001
    """The agent publishes the real quotes (quotes = {symbol: metrics}) + BTC 24h + sentiment."""
    if quotes:
        _data["quotes"] = quotes
        _data["btc_24h"] = btc_24h
        _data["fng"] = fng
        _data["ts"] = time.time()


def get() -> dict:
    """Snapshot of the most recent real market (empty until the agent's 1st cycle)."""
    return _data


def age_seconds() -> float:
    return time.time() - _data["ts"] if _data["ts"] else 1e9
