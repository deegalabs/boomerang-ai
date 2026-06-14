"""Agente SIMULADO AUTÔNOMO para o Console demo (público).

Representa FIELMENTE o agente real: o CÉREBRO decide sozinho e aplica as MESMAS regras
determinísticas do agente ao vivo — SL/TP dinâmico por volatilidade (R:R 1:2), sizing
por convicção, adaptação por regime e saída assimétrica (deixa o vencedor correr).
O usuário ASSISTE o agente operar e raciocinar; não dirige na mão.

Tudo SIMULADO: mercado por random-walk, banca fictícia de $100, zero API/carteira real.
Estado em memória por sessão (zera ao reiniciar o servidor — ok para demo).
"""
from __future__ import annotations

import random
import time

from boomerang.risk.risk_engine import (
    conviction_size_pct, dynamic_sl_tp, market_regime, tier_from_var)

START_CASH = 100.0       # banca simulada
MAX_POSITIONS = 3        # mesmo teto do agente real
BASE_POS_PCT = 22.0      # âncora do sizing por convicção

# Universo simulado (símbolo → preço-base). Só pra a simulação parecer real.
_UNIVERSE = {
    "ETH": 3200.0, "LINK": 18.0, "UNI": 12.0, "ADA": 0.62, "AVAX": 38.0, "DOT": 7.5,
    "DOGE": 0.16, "SHIB": 0.000022, "FLOKI": 0.00018, "TWT": 1.1, "LTC": 85.0, "XRP": 2.1,
}

_agents: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def _agent(addr: str) -> dict:
    if addr not in _agents:
        mkt = {s: {"price": p, "bias": random.uniform(-0.3, 0.5)} for s, p in _UNIVERSE.items()}
        _agents[addr] = {
            "cash": START_CASH, "positions": [], "trades": [], "feed": [],
            "running": False, "paused": False, "peak": START_CASH, "tick": 0,
            "market": mkt, "btc_bias": random.uniform(-1.0, 3.0), "regime": "NEUTRO",
            "config": {"focus": "ALL", "stop": 4, "tp": 0},
        }
    return _agents[addr]


def _log(a: dict, kind: str, text: str) -> None:
    a["feed"].insert(0, {"kind": kind, "text": text, "ts": _now()})
    a["feed"] = a["feed"][:40]


def _equity(a: dict) -> float:
    return a["cash"] + sum(p["qty"] * a["market"][p["symbol"]]["price"] for p in a["positions"])


# ── controles (o usuário assiste; pode ativar/pausar/parar) ───────────────────
def start(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    a["running"], a["paused"] = True, False
    _log(a, "sys", "🚀 Agente autônomo ATIVADO — 3 escudos online, escaneando o mercado.")
    return True, "Agente autônomo ativado (simulação)."


def pause(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    a["paused"] = not a["paused"]
    _log(a, "sys", "⏸️ Pausado pelo dono." if a["paused"] else "▶️ Retomado.")
    return True, ("Pausado (simulação)." if a["paused"] else "Retomado (simulação).")


def configure(addr: str, focus: str, stop: float, tp: float) -> tuple[bool, str]:
    a = _agent(addr)
    a["config"] = {"focus": focus, "stop": float(stop), "tp": float(tp)}
    return True, "Preferências salvas — o agente calibra SL/TP sozinho pela volatilidade."


def _close(a: dict, pos: dict, reason: str) -> float:
    cur = a["market"][pos["symbol"]]["price"]
    pnl = (cur - pos["entry_price"]) / pos["entry_price"] * 100.0 if pos["entry_price"] else 0.0
    a["cash"] += pos["qty"] * cur
    a["positions"].remove(pos)
    a["trades"].append({"type": "close", "symbol": pos["symbol"], "pnl_pct": pnl, "ts": _now()})
    emo = "🟢" if pnl >= 0 else "🔴"
    _log(a, "sell", f"{emo} VENDEU {pos['symbol']} — {reason} · PnL {pnl:+.1f}%")
    return pnl


def withdraw(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    for p in list(a["positions"]):
        _close(a, p, "saque")
    a["paused"], a["running"] = True, False
    _log(a, "sys", "🪃 Saque: tudo de volta ao caixa, agente parado.")
    return True, "Saque simulado: tudo no caixa e agente parado."


def panic(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    for p in list(a["positions"]):
        _close(a, p, "pânico (liquidação)")
    a["paused"], a["running"] = True, False
    _log(a, "sys", "🚨 PÂNICO: liquidou tudo e travou.")
    return True, "Pânico simulado: liquidou tudo e travou."


# ── 1 CICLO do agente autônomo (o coração da demo fiel) ───────────────────────
def tick(addr: str) -> dict:
    a = _agent(addr)
    if not a["running"] or a["paused"]:
        return snapshot(addr)
    a["tick"] += 1

    # 1) mercado anda (random-walk com REVERSÃO À MÉDIA — moves realistas, sem disparar)
    for m in a["market"].values():
        m["bias"] = m["bias"] * 0.88 + random.uniform(-0.22, 0.24)   # decai + ruído
        m["bias"] = max(-1.2, min(m["bias"], 1.4))
        m["price"] = max(m["price"] * (1 + (m["bias"] + random.uniform(-0.8, 0.8)) / 100.0), 1e-9)
    a["btc_bias"] = max(-7.0, min(a["btc_bias"] * 0.9 + random.uniform(-0.7, 0.7), 6.0))
    regime_lbl, cut_adj = market_regime(a["btc_bias"])
    a["regime"] = regime_lbl

    # 2) gere as posições — SAÍDA ASSIMÉTRICA: corta perda curta, deixa o ganho correr
    for pos in list(a["positions"]):
        cur = a["market"][pos["symbol"]]["price"]
        pos["peak"] = max(pos.get("peak", pos["entry_price"]), cur)
        pnl = (cur - pos["entry_price"]) / pos["entry_price"] * 100.0
        if not pos["trailing"] and pnl >= 3.0:           # ativa o trailing em +3%
            pos["trailing"] = True
            pos["stop"] = max(pos["stop"], pos["entry_price"])  # break-even
            _log(a, "skill", f"📈 {pos['symbol']} +3% → trailing ON (deixando o vencedor correr)")
        if pos["trailing"]:
            pos["stop"] = max(pos["stop"], pos["peak"] * (1 - pos["sl_pct"] / 100.0))
        if cur <= pos["stop"]:
            _close(a, pos, "trailing (lucro protegido)" if pos["trailing"] else "stop-loss disparado")

    # 3) gate macro: BTC despencando → risk-off, não abre
    if a["btc_bias"] <= -5.0:
        _log(a, "scan", f"🛡️ Gate MACRO: BTC {a['btc_bias']:+.1f}%/24h — risk-off, sem novas entradas.")
        a["peak"] = max(a["peak"], _equity(a))
        return snapshot(addr)

    # 4) escaneia + o CÉREBRO decide (melhor oportunidade relativa)
    ranked = sorted(a["market"].items(), key=lambda kv: kv[1]["bias"], reverse=True)
    held = {p["symbol"] for p in a["positions"]}
    cand = next((s for s, _ in ranked if s not in held), None)
    cut = max(58 + cut_adj, 48)  # barra adaptativa (regime desloca)

    if a["cash"] < 5.0 or len(a["positions"]) >= MAX_POSITIONS:
        # totalmente alocado → mostra a gestão (e rotação ocasional)
        if a["positions"] and cand and random.random() < 0.18:
            weak = min(a["positions"], key=lambda p: a["market"][p["symbol"]]["bias"])
            if a["market"][weak["symbol"]]["bias"] < 0.2 and not weak["trailing"]:
                _log(a, "skill", f"🔁 Rotação: {weak['symbol']} fraco → liberar capital p/ {cand}")
                _close(a, weak, "rotação (capital p/ melhor oportunidade)")
        elif random.random() < 0.3:  # não floodar o feed quando está só gerindo
            _log(a, "scan", f"🔍 Regime {regime_lbl} · {len(a['positions'])} posições · "
                            "deixando os vencedores correrem.")
    elif cand:
        bias = a["market"][cand]["bias"]
        score = int(max(40, min(58 + bias * 13 + random.uniform(-5, 7), 93)))
        var24 = round(abs(bias) * 6.0, 1)  # |Var24h| simulado
        if score >= cut:
            tier = tier_from_var(var24)
            sl, _tp = dynamic_sl_tp(tier, var24)
            conv = conviction_size_pct(BASE_POS_PCT, score, 40.0)
            amount = min(a["cash"], a["peak"] * conv / 100.0)
            price = a["market"][cand]["price"]
            a["cash"] -= amount
            a["positions"].append({
                "symbol": cand, "entry_price": price, "amount_usd": amount, "qty": amount / price,
                "stop": price * (1 - sl / 100.0), "sl_pct": sl, "peak": price,
                "trailing": False, "score": score, "tier": tier, "opened_at": _now(),
            })
            a["trades"].append({"type": "open", "symbol": cand, "amount_usd": amount, "ts": _now()})
            _log(a, "buy", f"✅ COMPREI {cand} ${amount:.0f} · score {score} · vol {tier} · "
                           f"SL {sl:.0f}%/alvo correndo · convicção {conv:.0f}%")
        else:
            _log(a, "scan", f"🔍 Regime {regime_lbl} · melhor: {cand} (score {score} < corte {cut}) "
                            "— sem setup, aguardando.")
    a["peak"] = max(a["peak"], _equity(a))
    return snapshot(addr)


def snapshot(addr: str) -> dict:
    a = _agent(addr)
    positions, holdings = [], []
    pos_val = 0.0
    for p in a["positions"]:
        cur = a["market"][p["symbol"]]["price"]
        val = p["qty"] * cur
        pos_val += val
        pnl = (cur - p["entry_price"]) / p["entry_price"] * 100.0 if p["entry_price"] else 0.0
        positions.append({
            "symbol": p["symbol"], "entry_price": p["entry_price"], "current_price": cur,
            "amount_usd": p["amount_usd"], "value_usd": val, "pnl_pct": pnl,
            "sl_pct": p["sl_pct"], "tier": p["tier"], "score": p["score"],
            "trailing_active": p["trailing"],
        })
        holdings.append({"symbol": p["symbol"], "kind": "token", "value_usd": val})
    equity = a["cash"] + pos_val
    holdings.insert(0, {"symbol": "USDC", "kind": "stable", "value_usd": a["cash"]})
    total = equity or 1.0
    for h in holdings:
        h["pct"] = h["value_usd"] / total * 100.0
    a["peak"] = max(a["peak"], equity)
    dd = max((a["peak"] - equity) / a["peak"] * 100.0, 0.0) if a["peak"] > 0 else 0.0
    state = ("PAUSED" if a["paused"] else
             ("SCANNING" if a["running"] and not a["positions"] else
              ("IN_POSITION" if a["positions"] else "READY")))
    return {
        "state": {
            "state": state, "equity_usd": equity, "drawdown_pct": dd, "peak_equity": a["peak"],
            "agent_address": addr, "holdings": holdings, "positions": positions,
            "running": a["running"], "paused": a["paused"], "regime": a["regime"],
            "btc_24h": round(a["btc_bias"], 1),
        },
        "feed": a["feed"],
        "trades": a["trades"],
    }
