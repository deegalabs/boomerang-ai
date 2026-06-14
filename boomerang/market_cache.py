"""Cache em-processo das cotações REAIS que o agente (Track 1) já busca na CMC.

A thread do agente escreve aqui a cada ciclo; a thread do site (demo) lê — mesmo
processo, mesmo objeto de módulo. Assim a demo opera sobre o MERCADO REAL (preços e
variações de verdade) SEM nenhuma chamada extra à CoinMarketCap (custo zero).
"""
from __future__ import annotations

import time

_data: dict = {"quotes": {}, "btc_24h": None, "ts": 0.0}


def put(quotes: dict, btc_24h) -> None:  # noqa: ANN001
    """O agente publica as cotações reais (quotes = {symbol: metrics}) + BTC 24h."""
    if quotes:
        _data["quotes"] = quotes
        _data["btc_24h"] = btc_24h
        _data["ts"] = time.time()


def get() -> dict:
    """Snapshot do mercado real mais recente (vazio até o 1º ciclo do agente)."""
    return _data


def age_seconds() -> float:
    return time.time() - _data["ts"] if _data["ts"] else 1e9
