"""AUTONOMOUS SIMULATED agent for the (public) demo Console, operating on the REAL MARKET.

It reads the REAL quotes the agent (Track 1) already fetches from CoinMarketCap (via
market_cache, same process) — so the DECISIONS are over real data: real prices, 24h changes
and BTC regime, with the SAME functions as the bot (momentum_prescore, dynamic_sl_tp,
conviction_size_pct, market_regime). Zero cost: no extra calls to CMC.

The wallet is fictional ($100), per session; a micro-movement between updates gives the
screen some liveliness (the real updates arrive every agent cycle, ~5 min).
If the agent hasn't published data yet, it falls back to a simulated market (the demo never breaks).
"""
from __future__ import annotations

import random
import time

from boomerang import market_cache
from boomerang.brain.cmc_analyzer import momentum_prescore
from boomerang.risk.risk_engine import (
    conviction_size_pct, dynamic_sl_tp, market_regime, tier_from_var)
from boomerang.strategy.confluence import evaluate_confluence
from boomerang.strategy.indicators import compute_indicators
from boomerang.strategy.klines import fetch_klines

START_CASH = 100.0
MAX_POSITIONS = 3
BASE_POS_PCT = 22.0

# Simulated fallback (only if the agent hasn't published real quotes yet).
_FALLBACK = {
    "ETH": 3200.0, "LINK": 18.0, "UNI": 12.0, "ADA": 0.62, "AVAX": 38.0, "DOT": 7.5,
    "DOGE": 0.16, "SHIB": 0.000022, "FLOKI": 0.00018, "TWT": 1.1, "LTC": 85.0, "XRP": 2.1,
}

_agents: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def _market() -> tuple[dict, float | None, int | None, bool]:
    """REAL market from the agent cache: ({symbol: metrics}, btc_24h, fng, real?).
    Simulated fallback if the agent hasn't published anything yet."""
    c = market_cache.get()
    q = c.get("quotes") or {}
    real = {s: m for s, m in q.items() if (m.get("price_usd") or 0) > 0}
    if real:
        return real, c.get("btc_24h"), c.get("fng"), True
    sim = {s: {"price_usd": p, "percent_change_24h": random.uniform(-6, 6),
               "percent_change_1h": random.uniform(-1, 1), "percent_change_7d": random.uniform(-10, 10),
               "volume_24h_usd": 1e8, "volume_change_24h_pct": random.uniform(-20, 40),
               "market_cap_usd": 1e9} for s, p in _FALLBACK.items()}
    return sim, random.uniform(-2.0, 3.0), random.randint(20, 75), False


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
    """Current price = REAL anchor × session micro-wobble (liveliness between updates)."""
    base = float((market.get(sym) or {}).get("price_usd") or 0.0)
    return base * (1 + a["wob"].get(sym, 0.0) / 100.0)


def _log(a: dict, kind: str, text: str) -> None:
    a["feed"].insert(0, {"kind": kind, "text": text, "ts": _now()})
    a["feed"] = a["feed"][:40]


def _equity(a: dict, market: dict) -> float:
    return a["cash"] + sum(p["qty"] * _price(a, market, p["symbol"]) for p in a["positions"])


# ── controls ─────────────────────────────────────────────────────────────────
def start(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    a["running"], a["paused"] = True, False
    _, _, _, real = _market()
    fonte = "REAL market (CoinMarketCap)" if real else "simulated market (agent still warming up)"
    _log(a, "sys", f"🚀 Autonomous agent ACTIVATED — reading {fonte}, 3 shields online.")
    return True, "Autonomous agent activated."


def pause(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    a["paused"] = not a["paused"]
    _log(a, "sys", "⏸️ Paused by the owner." if a["paused"] else "▶️ Resumed.")
    return True, ("Paused." if a["paused"] else "Resumed.")


def configure(addr: str, focus: str, stop: float, tp: float) -> tuple[bool, str]:
    a = _agent(addr)
    a["config"] = {"focus": focus, "stop": float(stop), "tp": float(tp)}
    return True, "Preferences saved — the agent calibrates SL/TP on its own by volatility."


def _close(a: dict, market: dict, pos: dict, reason: str) -> float:
    cur = _price(a, market, pos["symbol"])
    pnl = (cur - pos["entry_price"]) / pos["entry_price"] * 100.0 if pos["entry_price"] else 0.0
    a["cash"] += pos["qty"] * cur
    a["positions"].remove(pos)
    a["trades"].append({"type": "close", "symbol": pos["symbol"], "pnl_pct": pnl, "ts": _now()})
    emo = "🟢" if pnl >= 0 else "🔴"
    _log(a, "sell", f"{emo} SOLD {pos['symbol']} — {reason} · PnL {pnl:+.1f}%")
    return pnl


def withdraw(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    market, _, _, _ = _market()
    for p in list(a["positions"]):
        _close(a, market, p, "withdrawal")
    a["paused"], a["running"] = True, False
    return True, "Simulated withdrawal: everything in cash and agent stopped."


def panic(addr: str) -> tuple[bool, str]:
    a = _agent(addr)
    market, _, _, _ = _market()
    for p in list(a["positions"]):
        _close(a, market, p, "panic (liquidation)")
    a["paused"], a["running"] = True, False
    _log(a, "sys", "🚨 PANIC: liquidated everything and halted.")
    return True, "Simulated panic: liquidated everything and halted."


# ── 1 CYCLE of the autonomous agent over the REAL MARKET ───────────────────────────
def _confluence(sym: str, macro: str):
    """Same TA confluence engine the real agent uses (Binance 1m candles). None when the
    token has no candle data — then the demo falls back to the momentum-only path."""
    try:
        ks = fetch_klines(sym, "1m", 60)
        if not ks or len(ks) < 30:
            return None
        return evaluate_confluence(compute_indicators(ks), macro_regime=macro)
    except Exception:  # noqa: BLE001
        return None


def tick(addr: str) -> dict:
    a = _agent(addr)
    if not a["running"] or a["paused"]:
        return snapshot(addr)
    a["tick"] += 1
    market, btc24, fng, real = _market()
    a["real"] = real

    # per-token micro-wobble (liveliness only — SMALL so it doesn't blow the tight stops)
    for sym in market:
        w = a["wob"].get(sym, 0.0)
        a["wob"][sym] = max(-0.6, min(w * 0.85 + random.uniform(-0.16, 0.16), 0.6))

    regime_lbl, cut_adj = market_regime(btc24, fng)

    # ASYMMETRIC EXIT: cut the loss short, let the gain run (trailing)
    for pos in list(a["positions"]):
        if pos["symbol"] not in market:
            continue
        cur = _price(a, market, pos["symbol"])
        pos["peak"] = max(pos.get("peak", pos["entry_price"]), cur)
        pnl = (cur - pos["entry_price"]) / pos["entry_price"] * 100.0
        if not pos["trailing"] and pnl >= 3.0:
            pos["trailing"] = True
            pos["stop"] = max(pos["stop"], pos["entry_price"])
            _log(a, "skill", f"📈 {pos['symbol']} +3% → trailing ON (letting the winner run)")
        if pos["trailing"]:
            pos["stop"] = max(pos["stop"], pos["peak"] * (1 - pos["sl_pct"] / 100.0))
        if cur <= pos["stop"]:
            _close(a, market, pos, "trailing (profit protected)" if pos["trailing"] else "stop-loss triggered")

    # MACRO GATE: BTC plunging → risk-off
    if btc24 is not None and btc24 <= -5.0:
        _log(a, "scan", f"🛡️ MACRO gate: BTC {btc24:+.1f}%/24h — risk-off, no new entries.")
        a["peak"] = max(a["peak"], _equity(a, market))
        return snapshot(addr)

    # SCANS + decides (REAL prescore, best relative opportunity)
    ranked = sorted(market.keys(), key=lambda s: momentum_prescore(market[s]), reverse=True)
    held = {p["symbol"] for p in a["positions"]}
    cand = next((s for s in ranked if s not in held), None)
    cut = max(58 + cut_adj, 48)

    if a["cash"] < 5.0 or len(a["positions"]) >= MAX_POSITIONS:
        if a["positions"] and cand and random.random() < 0.2:
            weak = min(a["positions"], key=lambda p: momentum_prescore(market.get(p["symbol"]) or {}))
            wpre = momentum_prescore(market.get(weak["symbol"]) or {})
            cpre = momentum_prescore(market[cand])
            # only rotate to something CLEARLY stronger (and never a winner mid-run)
            if wpre < 12 and cpre >= wpre + 12 and not weak["trailing"]:
                _log(a, "skill", f"🔁 Rotation: {weak['symbol']} (weak) → free up capital for {cand} (strong)")
                _close(a, market, weak, "rotation (capital to a better opportunity)")
        elif random.random() < 0.3:
            _log(a, "scan", f"🔍 Regime {regime_lbl} · {len(a['positions'])} positions · "
                            "letting the winners run.")
    elif cand:
        m = market[cand]
        pre = momentum_prescore(m)
        ch24 = float(m.get("percent_change_24h") or 0.0)
        # Mirror of the real brain: young momentum scores, but OVEREXTENSION (rose too much =
        # top risk) PENALIZES — it doesn't buy the top of the pump.
        overext = max(0.0, ch24 - 12.0) * 1.6
        score = int(max(40, min(56 + pre * 0.7 + min(ch24, 12.0) * 0.6 - overext
                                 + random.uniform(-4, 5), 90)))
        var24 = round(abs(ch24), 1)
        # SAME confluence brain as the real Filter 1: veto pumps, scale the bet by TA agreement.
        macro = "BULL" if cut_adj < 0 else "DEFENSIVE" if cut_adj > 0 else "NEUTRAL"
        conf = _confluence(cand, macro)
        if conf and conf.decision == "AVOID":
            _log(a, "scan", f"🛡️ {cand}: TA veto — {conf.veto} · no chase.")
        elif score >= cut:
            tier = tier_from_var(var24)
            sl, _tp = dynamic_sl_tp(tier, var24)
            conv = conviction_size_pct(BASE_POS_PCT, score, 40.0)
            if conf:
                conv *= 1.15 if conf.enter else (0.75 if conf.decision == "WAIT" else 1.0)
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
                ta = f" · TA {conf.score:.0f}/100 [{conf.mode}]" if conf else ""
                _log(a, "buy", f"✅ BOUGHT {cand} ${amount:.0f} · score {score}{ta} · "
                               f"SL {sl:.0f}% · conviction {conv:.0f}% · 24h {ch24:+.1f}%")
                if conf and conf.reasons:
                    _log(a, "ta", "   ↳ " + " · ".join(conf.reasons[:3]))
        else:
            _log(a, "scan", f"🔍 Regime {regime_lbl} · best: {cand} (score {score} < cut {cut}) "
                            f"· 24h {float(m.get('percent_change_24h') or 0):+.1f}% — no setup.")

    a["focus"] = a["positions"][0]["symbol"] if a["positions"] else cand
    a["peak"] = max(a["peak"], _equity(a, market))
    return snapshot(addr)


def snapshot(addr: str) -> dict:
    a = _agent(addr)
    market, btc24, fng, real = _market()
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
            "regime": market_regime(btc24, fng)[0], "btc_24h": round(btc24 or 0.0, 1), "fng": fng,
            "data_real": real,
            "analyzing": a.get("focus") or (positions[0]["symbol"] if positions else None),
        },
        "feed": a["feed"],
        "trades": a["trades"],
    }
