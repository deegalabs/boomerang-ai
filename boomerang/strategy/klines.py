"""Fetch 1-minute OHLCV candles from Binance's free public REST (no API key).

Used to feed the indicator library. Best-effort by design: a symbol that doesn't
trade on Binance (a pure on-chain token) simply returns ``[]`` and the caller falls
back to the candle-free path — it never blocks a cycle.
"""
from __future__ import annotations

import logging
import time

import httpx

from boomerang.strategy.indicators import Kline

_log = logging.getLogger("boomerang.klines")
_BASE = "https://api.binance.com/api/v3/klines"
_CACHE: dict[str, tuple[float, list[Kline]]] = {}
_TTL = 30.0  # seconds — a scan/monitor pass reuses the same candles

# our symbol → Binance base asset, when they differ (most match 1:1)
_ALIAS: dict[str, str] = {}


def _pair(symbol: str, quote: str) -> str:
    base = _ALIAS.get(symbol.upper(), symbol.upper())
    return f"{base}{quote}"


def fetch_klines(symbol: str, interval: str = "1m", limit: int = 60,
                 quote: str = "USDT", timeout: float = 8.0) -> list[Kline]:
    """Return the latest ``limit`` candles (oldest first), or ``[]`` on any issue.

    The final candle is the still-forming one (Binance includes it); indicators that
    care drop it themselves.
    """
    key = f"{symbol.upper()}{quote}-{interval}-{limit}"
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        r = httpx.get(_BASE, params={"symbol": _pair(symbol, quote),
                                     "interval": interval, "limit": limit}, timeout=timeout)
        if r.status_code != 200:
            _CACHE[key] = (now, [])  # cache the miss too (e.g. token not on Binance)
            return []
        out = [Kline(open=float(k[1]), high=float(k[2]), low=float(k[3]),
                     close=float(k[4]), volume=float(k[5])) for k in r.json()]
        _CACHE[key] = (now, out)
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("klines unavailable for %s: %s", symbol, exc)
        _CACHE[key] = (now, [])
        return []
