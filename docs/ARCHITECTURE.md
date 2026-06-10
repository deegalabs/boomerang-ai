# 🪃 Boomerang AI — Architecture

Autonomous short-cycle trading agent on BNB Chain, controlled from Telegram.
Mapped to the project's real files.

---

## 1. Macro view — two layers, one container

```
┌──────────────────────────────────────────────────────────────────────────┐
│  USER LAYER  (never touches private keys)                                  │
│                                                                            │
│   📱 Telegram Bot   boomerang/interface/telegram_bot.py                    │
│      • buttons (InlineKeyboards)   • MASTER_USER_ID pinning                 │
│      • /start /status /panic /pausar /sacar   • real-time alerts           │
└───────────────┬───────────────────────────────▲───────────────────────────┘
                │ control intents               │ alerts (AlertBus)
                │ (configure/start/panic/        │ boomerang/ipc/events.py
                ▼  withdraw)                      │
┌──────────────────────────────────────────────────────────────────────────┐
│  AGENT LAYER  (holds signing access via TWAK)                              │
│                                                                            │
│   🧠 Orchestrator   boomerang/agent.py                                      │
│      scan_loop (interval) + monitor_loop (2s)                               │
│                                                                            │
│   ┌─────────────┐   ┌──────────────┐   ┌───────────────┐                  │
│   │ FILTER 1    │ → │ FILTER 2     │ → │ FILTER 3      │                  │
│   │ CMC/Claude  │   │ BNB validate │   │ TWAK execute  │                  │
│   └─────────────┘   └──────────────┘   └───────────────┘                  │
│         ▲                  ▲                    ▲                          │
│         │           🛡️ RISK ENGINE (cross-cutting)                        │
│         │           boomerang/risk/risk_engine.py                          │
│         │   circuit breaker · sizing · trailing · heartbeat · mutex        │
└─────────┼──────────────────┼────────────────────┼─────────────────────────┘
          │                  │                    │
     CoinMarketCap       BNB Chain RPC         Trust Wallet
     (MCP + x402)        (PancakeSwap)         Agent Kit (twak)
```

**Isolation principle:** the bot/site (which talk to the internet) **never** access the
key. They only send *control intents* and receive *alerts*. The key lives in the
**encrypted keystore** of `twak`, on the agent side. (v1: in-process bus, a single
container in deployment; the seams allow real IPC between processes in the hardening
phase.)

> **Where it runs (deployment):** the official instance runs on **Railway**, agent + site
> in a single container. The encrypted keystore and the password live as protected
> provider environment variables (not in the repo or the image). Signing happens in the
> agent's environment, never in the browser/site. It remains **self-custody** (the agent's
> own wallet, withdrawals pinned to the owner), but the key does not stay "on your machine".

---

## 2. The lifecycle of a trade (the "customs")

Each scan cycle (`agent.run_cycle`) crosses three filters in series. A single rejection
aborts the trade **before** any money is touched.

```
                    ┌──────────────────────────────────────────────┐
  each cycle   →    │ 🛡️ RISK ENGINE (pre-check)                    │
                    │  • equity (twak portfolio) → update peak      │
                    │  • drawdown ≥ 23%? → PANIC (liquidate + halt) │
                    │  • heartbeat? (>20h without a trade)          │
                    │  • can open? (cooldown, #positions, stable)   │
                    └───────────────────┬──────────────────────────┘
                                        │ ok
   ┌────────────────────────────────────▼─────────────────────────────────┐
   │ 1️⃣ FILTER 1 — Brain (cmc_analyzer.py)                                  │
   │   fetch structured metrics from CMC (REST/MCP) → SANITIZE (anti-       │
   │   injection) → Claude (forced tool) → {confidence_score, action}      │
   │   deterministic cutoff: score < min → HOLD                            │
   └────────────────────────────────────┬──────────────────────────────────┘
                                         │ BUY (score ≥ 70 in conservative)
   ┌─────────────────────────────────────▼─────────────────────────────────┐
   │ 2️⃣ FILTER 2 — On-chain validation (bnb_validation.py)                 │
   │   whitelist · getAmountsOut (slippage) · round-trip (hidden tax) ·     │
   │   CMC×pool divergence (oracle) — all read-only, zero cost             │
   └─────────────────────────────────────┬─────────────────────────────────┘
                                          │ approved (min_out computed)
   ┌──────────────────────────────────────▼────────────────────────────────┐
   │ 3️⃣ FILTER 3 — Execution (twak_executor.py)                            │
   │   under a mutex: twak swap USDC→token (agent-side signing) → open      │
   │   position with initial stop-loss. Emits a TRADE_OPENED alert.        │
   └─────────────────────────────────────────────────────────────────────┘
```

---

## 3. The exit monitor (stop / trailing)

A parallel 2s loop (`agent.check_positions`), leveraging BSC's fast blocks:

```
for each position:
   price = bnb_validation.onchain_price_usd(token)     # read-only, no gas
   signal = risk.evaluate_position(position, price)
     ├─ HOLD                → keep
     ├─ SELL_STOP_LOSS      → dropped past the stop → sell
     ├─ SELL_TRAILING       → rose +5% (locks break-even, follows the peak),
     │                        then pulled back → sell IN PROFIT
     └─ SELL_TAKE_PROFIT    → hit the target → realize the gain
   if selling → twak swap token→USDC → TRADE_CLOSED alert (with PnL)
```

---

## 4. The flow of money (the "boomerang")

```
[ Personal Wallet ] ──deposit bankroll──► [ Agent Wallet ] ──trades──► PancakeSwap
       ▲                                        │
       └──────── /sacar  or  PANIC  ◄───────────┘  (twak transfer --confirm-to)
                 (converts to stable and sends it back)
```

- **Competition mode:** trades continuously, compounding the bankroll.
- **Boomerang (automatic return at cycle end):** a future/demo enhancement.
- **`--confirm-to`** pins the withdrawal destination = anti-drain shield.

---

## 5. The two layers of rules

| DEV layer (immutable, in code)             | USER layer (via Telegram)            |
|--------------------------------------------|--------------------------------------|
| eligible-token whitelist                   | token focus (liquid subset)          |
| global drawdown circuit breaker (23%/DQ 30%) | stop-loss (2% / 4% / 5%)           |
| slippage cap (0.5%)                        | mode (conservative ≥70 / aggressive ≥60) |
| destination lock (anti-drain)              | (per-trade size = % of equity)       |
| min trades / heartbeat                     |                                      |

`config.json` = `dev_safety` + `hackathon` (locked) and `user` (tunable).

---

## 6. Security hardening (threat model → defense)

| Attack                          | Defense (file)                                     |
|---------------------------------|----------------------------------------------------|
| Prompt injection (news/social)  | sanitize_metrics — numbers/labels only (cmc_analyzer)|
| Bot hijack                      | MASTER_USER_ID pinning (telegram_bot)              |
| Sandwich / MEV                  | slippage + amountOutMin (bnb_validation)           |
| Hidden tax / honeypot           | round-trip retention (bnb_validation)              |
| Stale oracle ("falling knife")  | CMC×pool divergence (bnb_validation)               |
| Infinite loop / gas spam        | mutex + cooldown (risk_engine)                     |
| Key theft                       | encrypted keystore in twak; bot/site have no access |
| Host exposure (cloud)           | secrets as protected env vars; never in repo/image; small bankroll bounds risk |
| Catastrophic drawdown / DQ      | deterministic circuit breaker (risk_engine)        |

---

## 7. Mapping to sponsors and prizes

- **CoinMarketCap (Agent Hub):** Filter 1 consumes data via REST/MCP and pays via x402
  → competes for "Best Use of Agent Hub".
- **Trust Wallet (TWAK):** the single execution layer, multiple surfaces (signing +
  autonomous mode + x402), self-custody → targets the "Best Use of TWAK" rubric.
- **BNB AI Agent SDK:** the agent's on-chain identity (ERC-8004) → "Best Use of BNB SDK".
- **Track 1 (PnL):** deterministic guardrails maximize return without breaching the
  drawdown that disqualifies.
