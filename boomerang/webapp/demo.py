"""Agente SIMULADO para o Console demo (público).

Cada carteira conectada ganha um agente próprio, isolado, com banca fictícia.
Nenhum dinheiro real, nenhuma custódia, nenhuma transação on-chain. Só para os
juízes/visitantes experimentarem a UX completa do produto com segurança.

Estado em memória por endereço (zera ao reiniciar o servidor — ok para demo).
"""
from __future__ import annotations

import math
import time

START_CASH = 100.0  # banca simulada inicial (USDC fictício)
TRADE_SIZE = 10.0   # tamanho de cada compra simulada

# Preços-base aproximados só para a simulação parecer real.
PRICES = {
    "ETH": 1700.0, "XRP": 1.17, "DOGE": 0.087, "ADA": 0.167, "LINK": 8.0,
    "LTC": 43.0, "AVAX": 6.8, "DOT": 1.0, "UNI": 2.5, "AAVE": 64.0,
    "ATOM": 1.7, "BCH": 208.0, "SHIB": 0.0000095, "FLOKI": 0.000025, "TWT": 0.38,
}

_AGENTS: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def _price(sym: str) -> float:
    """Preço com leve oscilação no tempo, para o PnL 'respirar'."""
    base = PRICES.get(sym, 1.0)
    drift = 0.04 * math.sin(_now() / 25.0 + (abs(hash(sym)) % 100) / 15.0)
    return base * (1 + drift)


def _agent(addr: str) -> dict:
    a = _AGENTS.get(addr)
    if a is None:
        a = {"cash": START_CASH, "positions": [], "trades": [],
             "config": {"focus": "ALL", "stop": 4, "tp": 10}, "paused": False, "peak": START_CASH}
        _AGENTS[addr] = a
    return a


def configure(addr: str, focus: str, stop: float, tp: float) -> tuple[bool, str]:
    a = _agent(addr)
    a["config"] = {"focus": focus, "stop": stop, "tp": tp}
    return True, "Configuração salva (simulação)."


def pause(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    a["paused"] = not a["paused"]
    return True, ("Agente pausado (simulação)." if a["paused"] else "Agente retomado (simulação).")


def buy(addr: str, sym: str, amount: float = TRADE_SIZE) -> tuple[bool, str]:
    a = _agent(addr)
    sym = (sym or "").upper()
    if sym not in PRICES:
        return False, "Moeda inválida."
    if a["paused"]:
        return False, "Agente pausado."
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = TRADE_SIZE
    if amount < 1:
        return False, "Valor mínimo de compra: $1."
    if a["cash"] < amount:
        return False, f"Saldo simulado insuficiente (você tem ${a['cash']:.2f})."
    price = _price(sym)
    a["cash"] -= amount
    qty = amount / price
    pos = next((p for p in a["positions"] if p["symbol"] == sym), None)
    if pos:  # já tem essa moeda: soma à posição (preço médio)
        pos["amount_usd"] += amount
        pos["qty"] += qty
        pos["entry_price"] = pos["amount_usd"] / pos["qty"]
        pos["stop_loss_price"] = pos["entry_price"] * (1 - a["config"]["stop"] / 100.0)
    else:
        a["positions"].append({
            "symbol": sym, "entry_price": price, "amount_usd": amount, "qty": qty,
            "stop_loss_price": price * (1 - a["config"]["stop"] / 100.0), "opened_at": _now(),
        })
    a["trades"].append({"type": "open", "symbol": sym, "amount_usd": amount, "ts": _now()})
    return True, f"Compra simulada de ${amount:.0f} em {sym}."


def sell(addr: str, sym: str) -> tuple[bool, str]:
    a = _agent(addr)
    sym = (sym or "").upper()
    pos = next((p for p in a["positions"] if p["symbol"] == sym), None)
    if not pos:
        return False, "Sem posição nessa moeda."
    cur = _price(sym)
    pnl_pct = (cur - pos["entry_price"]) / pos["entry_price"] * 100.0
    a["cash"] += pos["amount_usd"] * (1 + pnl_pct / 100.0)
    a["positions"].remove(pos)
    a["trades"].append({"type": "close", "symbol": sym, "pnl_pct": pnl_pct, "ts": _now()})
    return True, f"Venda simulada de {sym} (PnL {pnl_pct:+.1f}%)."


def withdraw(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    for p in list(a["positions"]):
        sell(addr, p["symbol"])
    a["paused"] = True
    return True, "Saque simulado: tudo voltou ao caixa e o agente pausou."


def panic(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    for p in list(a["positions"]):
        sell(addr, p["symbol"])
    a["paused"] = True
    return True, "Pânico simulado: liquidou tudo e travou."


ACTIONS = {"configure": None, "pause": pause, "buy": None, "sell": None,
           "withdraw": withdraw, "panic": panic}


def snapshot(addr: str) -> dict:
    a = _agent(addr)
    positions, holdings = [], []
    pos_val = 0.0
    for p in a["positions"]:
        cur = _price(p["symbol"])
        val = p["qty"] * cur
        pos_val += val
        pnl = (cur - p["entry_price"]) / p["entry_price"] * 100.0 if p["entry_price"] else 0.0
        positions.append({**p, "current_price": cur, "value_usd": val, "pnl_pct": pnl})
        holdings.append({"symbol": p["symbol"], "kind": "token", "value_usd": val})
    equity = a["cash"] + pos_val
    holdings.insert(0, {"symbol": "USDC", "kind": "stable", "value_usd": a["cash"]})
    total = equity or 1.0
    for h in holdings:
        h["pct"] = h["value_usd"] / total * 100.0
    a["peak"] = max(a["peak"], equity)
    dd = max((a["peak"] - equity) / a["peak"] * 100.0, 0.0) if a["peak"] > 0 else 0.0
    state = "PAUSED" if a["paused"] else ("IN_POSITION" if a["positions"] else "SCANNING")
    return {
        "state": {"state": state, "equity_usd": equity, "drawdown_pct": dd, "peak_equity": a["peak"],
                  "agent_address": addr, "holdings": holdings, "positions": positions,
                  "token_focus": a["config"]["focus"], "stop_loss_pct": a["config"]["stop"],
                  "take_profit_pct": a["config"]["tp"], "paused": a["paused"]},
        "trades": a["trades"],
    }
