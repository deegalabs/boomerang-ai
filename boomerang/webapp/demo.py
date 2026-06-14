"""Agente SIMULADO AUTÔNOMO para o Console demo (público), operando sobre o MERCADO REAL.

Lê as cotações REAIS que o agente (Track 1) já busca na CoinMarketCap (via market_cache,
mesmo processo) — então as DECISÕES são sobre dados de verdade: preços, variações 24h e
regime do BTC reais, com as MESMAS funções do robô (momentum_prescore, dynamic_sl_tp,
conviction_size_pct, market_regime). Custo zero: nenhuma chamada extra à CMC.

A carteira é fictícia ($100), por sessão; um micro-movimento entre atualizações dá
vivacidade à tela (as atualizações reais chegam a cada ciclo do agente, ~5 min).
Se o agente ainda não publicou dados, cai num mercado simulado (a demo nunca quebra).
"""
from __future__ import annotations

import random
import time

from boomerang import market_cache
from boomerang.brain.cmc_analyzer import momentum_prescore
from boomerang.risk.risk_engine import (
    conviction_size_pct, dynamic_sl_tp, market_regime, tier_from_var)

START_CASH = 100.0
MAX_POSITIONS = 3
BASE_POS_PCT = 22.0

# Fallback simulado (só se o agente ainda não publicou cotações reais).
_FALLBACK = {
    "ETH": 3200.0, "LINK": 18.0, "UNI": 12.0, "ADA": 0.62, "AVAX": 38.0, "DOT": 7.5,
    "DOGE": 0.16, "SHIB": 0.000022, "FLOKI": 0.00018, "TWT": 1.1, "LTC": 85.0, "XRP": 2.1,
}

_agents: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def _market() -> tuple[dict, float | None, bool]:
    """Mercado REAL do cache do agente: ({symbol: metrics}, btc_24h, real?).
    Fallback simulado se o agente ainda não publicou nada."""
    c = market_cache.get()
    q = c.get("quotes") or {}
    real = {s: m for s, m in q.items() if (m.get("price_usd") or 0) > 0}
    if real:
        return real, c.get("btc_24h"), True
    sim = {s: {"price_usd": p, "percent_change_24h": random.uniform(-6, 6),
               "percent_change_1h": random.uniform(-1, 1), "percent_change_7d": random.uniform(-10, 10),
               "volume_24h_usd": 1e8, "volume_change_24h_pct": random.uniform(-20, 40),
               "market_cap_usd": 1e9} for s, p in _FALLBACK.items()}
    return sim, random.uniform(-2.0, 3.0), False


def _agent(addr: str) -> dict:
    if addr not in _agents:
        _agents[addr] = {
            "cash": START_CASH, "positions": [], "trades": [], "feed": [],
            "running": False, "paused": False, "peak": START_CASH, "tick": 0,
            "wob": {}, "real": False,
            "config": {"focus": "ALL", "stop": 4, "tp": 0},
        }
    return _agents[addr]


def _price(a: dict, market: dict, sym: str) -> float:
    """Preço atual = âncora REAL × micro-wobble da sessão (vivacidade entre updates)."""
    base = float((market.get(sym) or {}).get("price_usd") or 0.0)
    return base * (1 + a["wob"].get(sym, 0.0) / 100.0)


def _log(a: dict, kind: str, text: str) -> None:
    a["feed"].insert(0, {"kind": kind, "text": text, "ts": _now()})
    a["feed"] = a["feed"][:40]


def _equity(a: dict, market: dict) -> float:
    return a["cash"] + sum(p["qty"] * _price(a, market, p["symbol"]) for p in a["positions"])


# ── controles ─────────────────────────────────────────────────────────────────
def start(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    a["running"], a["paused"] = True, False
    _, _, real = _market()
    fonte = "mercado REAL (CoinMarketCap)" if real else "mercado simulado (agente ainda aquecendo)"
    _log(a, "sys", f"🚀 Agente autônomo ATIVADO — lendo {fonte}, 3 escudos online.")
    return True, "Agente autônomo ativado."


def pause(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    a["paused"] = not a["paused"]
    _log(a, "sys", "⏸️ Pausado pelo dono." if a["paused"] else "▶️ Retomado.")
    return True, ("Pausado." if a["paused"] else "Retomado.")


def configure(addr: str, focus: str, stop: float, tp: float) -> tuple[bool, str]:
    a = _agent(addr)
    a["config"] = {"focus": focus, "stop": float(stop), "tp": float(tp)}
    return True, "Preferências salvas — o agente calibra SL/TP sozinho pela volatilidade."


def _close(a: dict, market: dict, pos: dict, reason: str) -> float:
    cur = _price(a, market, pos["symbol"])
    pnl = (cur - pos["entry_price"]) / pos["entry_price"] * 100.0 if pos["entry_price"] else 0.0
    a["cash"] += pos["qty"] * cur
    a["positions"].remove(pos)
    a["trades"].append({"type": "close", "symbol": pos["symbol"], "pnl_pct": pnl, "ts": _now()})
    emo = "🟢" if pnl >= 0 else "🔴"
    _log(a, "sell", f"{emo} VENDEU {pos['symbol']} — {reason} · PnL {pnl:+.1f}%")
    return pnl


def withdraw(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    market, _, _ = _market()
    for p in list(a["positions"]):
        _close(a, market, p, "saque")
    a["paused"], a["running"] = True, False
    return True, "Saque simulado: tudo no caixa e agente parado."


def panic(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    market, _, _ = _market()
    for p in list(a["positions"]):
        _close(a, market, p, "pânico (liquidação)")
    a["paused"], a["running"] = True, False
    _log(a, "sys", "🚨 PÂNICO: liquidou tudo e travou.")
    return True, "Pânico simulado: liquidou tudo e travou."


# ── 1 CICLO do agente autônomo sobre o MERCADO REAL ───────────────────────────
def tick(addr: str) -> dict:
    a = _agent(addr)
    if not a["running"] or a["paused"]:
        return snapshot(addr)
    a["tick"] += 1
    market, btc24, real = _market()
    a["real"] = real

    # micro-wobble por token (só vivacidade — PEQUENO p/ não estourar os stops curtos)
    for sym in market:
        w = a["wob"].get(sym, 0.0)
        a["wob"][sym] = max(-0.6, min(w * 0.85 + random.uniform(-0.16, 0.16), 0.6))

    regime_lbl, cut_adj = market_regime(btc24)

    # SAÍDA ASSIMÉTRICA: corta perda curta, deixa o ganho correr (trailing)
    for pos in list(a["positions"]):
        if pos["symbol"] not in market:
            continue
        cur = _price(a, market, pos["symbol"])
        pos["peak"] = max(pos.get("peak", pos["entry_price"]), cur)
        pnl = (cur - pos["entry_price"]) / pos["entry_price"] * 100.0
        if not pos["trailing"] and pnl >= 3.0:
            pos["trailing"] = True
            pos["stop"] = max(pos["stop"], pos["entry_price"])
            _log(a, "skill", f"📈 {pos['symbol']} +3% → trailing ON (deixando o vencedor correr)")
        if pos["trailing"]:
            pos["stop"] = max(pos["stop"], pos["peak"] * (1 - pos["sl_pct"] / 100.0))
        if cur <= pos["stop"]:
            _close(a, market, pos, "trailing (lucro protegido)" if pos["trailing"] else "stop-loss disparado")

    # GATE MACRO: BTC despencando → risk-off
    if btc24 is not None and btc24 <= -5.0:
        _log(a, "scan", f"🛡️ Gate MACRO: BTC {btc24:+.1f}%/24h — risk-off, sem novas entradas.")
        a["peak"] = max(a["peak"], _equity(a, market))
        return snapshot(addr)

    # ESCANEIA + decide (prescore REAL, melhor oportunidade relativa)
    ranked = sorted(market.keys(), key=lambda s: momentum_prescore(market[s]), reverse=True)
    held = {p["symbol"] for p in a["positions"]}
    cand = next((s for s in ranked if s not in held), None)
    cut = max(58 + cut_adj, 48)

    if a["cash"] < 5.0 or len(a["positions"]) >= MAX_POSITIONS:
        if a["positions"] and cand and random.random() < 0.2:
            weak = min(a["positions"], key=lambda p: momentum_prescore(market.get(p["symbol"]) or {}))
            wpre = momentum_prescore(market.get(weak["symbol"]) or {})
            cpre = momentum_prescore(market[cand])
            # só rotaciona p/ algo CLARAMENTE mais forte (e nunca um vencedor em corrida)
            if wpre < 12 and cpre >= wpre + 12 and not weak["trailing"]:
                _log(a, "skill", f"🔁 Rotação: {weak['symbol']} (fraco) → liberar capital p/ {cand} (forte)")
                _close(a, market, weak, "rotação (capital p/ melhor oportunidade)")
        elif random.random() < 0.3:
            _log(a, "scan", f"🔍 Regime {regime_lbl} · {len(a['positions'])} posições · "
                            "deixando os vencedores correrem.")
    elif cand:
        m = market[cand]
        pre = momentum_prescore(m)
        ch24 = float(m.get("percent_change_24h") or 0.0)
        # Mirror do cérebro real: momentum jovem pontua, mas ESTICAMENTO (subiu demais =
        # risco de topo) PENALIZA — não compra o topo do pump.
        overext = max(0.0, ch24 - 12.0) * 1.6
        score = int(max(40, min(56 + pre * 0.7 + min(ch24, 12.0) * 0.6 - overext
                                 + random.uniform(-4, 5), 90)))
        var24 = round(abs(ch24), 1)
        if score >= cut:
            tier = tier_from_var(var24)
            sl, _tp = dynamic_sl_tp(tier, var24)
            conv = conviction_size_pct(BASE_POS_PCT, score, 40.0)
            amount = min(a["cash"], a["peak"] * conv / 100.0)
            price = _price(a, market, cand)
            if price > 0 and amount >= 5:
                a["cash"] -= amount
                a["positions"].append({
                    "symbol": cand, "entry_price": price, "amount_usd": amount, "qty": amount / price,
                    "stop": price * (1 - sl / 100.0), "sl_pct": sl, "peak": price,
                    "trailing": False, "score": score, "tier": tier, "opened_at": _now(),
                })
                a["trades"].append({"type": "open", "symbol": cand, "amount_usd": amount, "ts": _now()})
                _log(a, "buy", f"✅ COMPREI {cand} ${amount:.0f} · score {score} · vol {tier} · "
                               f"SL {sl:.0f}%/alvo correndo · convicção {conv:.0f}% · 24h {ch24:+.1f}%")
        else:
            _log(a, "scan", f"🔍 Regime {regime_lbl} · melhor: {cand} (score {score} < corte {cut}) "
                            f"· 24h {float(m.get('percent_change_24h') or 0):+.1f}% — sem setup.")

    a["peak"] = max(a["peak"], _equity(a, market))
    return snapshot(addr)


def snapshot(addr: str) -> dict:
    a = _agent(addr)
    market, btc24, real = _market()
    positions, holdings = [], []
    pos_val = 0.0
    for p in a["positions"]:
        cur = _price(a, market, p["symbol"]) or p["entry_price"]
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
            "running": a["running"], "paused": a["paused"],
            "regime": market_regime(btc24)[0], "btc_24h": round(btc24 or 0.0, 1),
            "data_real": real,
        },
        "feed": a["feed"],
        "trades": a["trades"],
    }
