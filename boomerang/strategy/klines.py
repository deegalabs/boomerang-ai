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
# data-api.binance.vision is Binance's public market-data host — globally reachable,
# no API key, and NOT geo-blocked (api.binance.com returns 451 from US/cloud regions
# like Railway). We try it first, then fall back to the main host.
_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
_PATH = "/api/v3/klines"
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
    params = {"symbol": _pair(symbol, quote), "interval": interval, "limit": limit}
    for host in _HOSTS:
        try:
            r = httpx.get(host + _PATH, params=params, timeout=timeout)
            if r.status_code == 200:
                out = [Kline(open=float(k[1]), high=float(k[2]), low=float(k[3]),
                             close=float(k[4]), volume=float(k[5])) for k in r.json()]
                _CACHE[key] = (now, out)
                return out
            if r.status_code in (400, 404):
                break  # symbol simply doesn't trade here — don't bother the fallback host
        except Exception as exc:  # noqa: BLE001
            _log.warning("klines host %s failed for %s: %s", host, symbol, exc)
    _CACHE[key] = (now, [])  # cache the miss (token not on Binance, or all hosts down)
    return []
