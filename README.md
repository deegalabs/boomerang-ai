# 🪃 Boomerang AI

> **Autonomous short-cycle trading agent on BNB Smart Chain, controlled from Telegram.**
> It reads attention signals on **CoinMarketCap**, decides with **Claude**, and signs/executes self-custody swaps via the **Trust Wallet Agent Kit**. Capital "goes to the market and comes back" — like a boomerang.

**Hackathon:** BNB Hack — AI Trading Agent Edition (CoinMarketCap × Trust Wallet × BNB Chain) — **Track 1: Autonomous Trading Agents.**

🌐 **Live:** https://boomerang-ai-production.up.railway.app · runs 24/7 in the cloud, controlled by Telegram.

---

## On-chain proof (mainnet, verifiable)

Everything the agent claims is verifiable on-chain. The agent wallet is `0xc72a37f4bb7c454Fd8a9EB629aFaEeb101F67dff`.

| What | Proof |
|---|---|
| **Agent identity (ERC-8004)** | agentId **131071** on BNB mainnet — [registration tx](https://bscscan.com/tx/0x93b2d496350f23aafc0872e0d6e5b0d736d0cb76260fd33f957b79bbe8f66947) · registry `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` |
| **x402 micropayment (real)** | $0.01 USDC settled on Base for CoinMarketCap data — [settlement tx](https://basescan.org/tx/0xd5b04f9e12610160aed646a703a28f3625adbcfff86d8e54fde7f6835a76a699) |
| **Sell (ADA → USDC)** | [tx](https://bscscan.com/tx/0x7f87dec9e271461b3f1205440cfa26f531da4671e6ad2380966aa3627311da21) |
| **Sell (ATOM → USDC)** | [tx](https://bscscan.com/tx/0x44b3adbe2bfa9c514a0e75e380a6c78e627c5ac02b696bc559fe1037e9118602) |
| **Capital return to owner** | anti-drain transfer — [tx](https://bscscan.com/tx/0xbf751fefd833b17c8f37c98f1157b223576240298c8c0f6aec50c7e0a5ee2df9) |

The full lifecycle is proven on-chain: **buy → sell → return to owner**, plus the **identity** and **x402** integrations.

---

## The thesis

**Attention arbitrage.** Exploit the lag between a spike in retail attention on CoinMarketCap (searches, trending, sentiment, momentum) and the liquidity arriving on-chain on BNB Smart Chain. Directional **spot** entries on liquid tokens, with deterministic risk management. No leverage, no derivatives.

---

## How it works — the "customs" pipeline

Every scan cycle runs three filters in series. A single rejection aborts the trade **before** any money moves.

```
                    ┌──────────────────────────────────────────────┐
  each cycle   →    │ 🛡️ RISK ENGINE (pre-check)                    │
                    │  • equity (on-chain) → update peak            │
                    │  • drawdown ≥ 23%? → PANIC (liquidate + halt) │
                    │  • heartbeat? (min trades/day)                │
                    │  • can open? (cooldown, #positions, stable)   │
                    └───────────────────┬──────────────────────────┘
                                        │ ok
   ┌────────────────────────────────────▼─────────────────────────────────┐
   │ 1️⃣  FILTER 1 — Brain (brain/cmc_analyzer.py)                          │
   │   fetch structured metrics from CoinMarketCap (REST + x402/MCP) →      │
   │   SANITIZE (anti prompt-injection) → Claude (forced tool) →            │
   │   {confidence_score, action}. Deterministic cutoff: score < min → HOLD │
   └────────────────────────────────────┬──────────────────────────────────┘
                                         │ BUY (score ≥ 70, conservative)
   ┌─────────────────────────────────────▼─────────────────────────────────┐
   │ 2️⃣  FILTER 2 — On-chain validation (vault/bnb_validation.py)          │
   │   liquidity (V2 + V3 via TWAK aggregator) · round-trip (hidden-tax     │
   │   detection) · slippage · oracle divergence (CMC vs pool) — read-only  │
   └─────────────────────────────────────┬─────────────────────────────────┘
                                          │ approved
   ┌──────────────────────────────────────▼────────────────────────────────┐
   │ 3️⃣  FILTER 3 — Execution (vault/twak_executor.py)                     │
   │   under a mutex: TWAK swap USDC→token (agent-side signing) → open       │
   │   position with stop-loss. A 2s monitor handles trailing / take-profit. │
   └─────────────────────────────────────────────────────────────────────┘
```

**Risk engine** (`risk/risk_engine.py`, cross-cutting): global drawdown circuit breaker, position sizing capped by the **real stable balance**, anti-loop mutex, trade cooldown, activity heartbeat, position reconciliation against the wallet.

---

## Architecture & deployment

Two **logical** layers, one **physical** container:

```
[ USER LAYER ]   Telegram bot + public website (never touches the key)
       │  control intents / config / alerts          ▲ read-only proof
       ▼                                              │
[ AGENT LAYER ]  the vault — holds the (encrypted) key
   Filter 1 (CMC/Claude) → Filter 2 (BNB) → Filter 3 (TWAK)
```

- **Deployment:** a single service on **Railway** runs both the **public site** (uvicorn) and the **agent** (own thread), sharing a volume for state. The encrypted TWAK keystore and secrets are injected as **protected environment variables at runtime** — never in the repository or the image (`.dockerignore`).
- **Why not Vercel:** the site is server-rendered Python (Starlette + Jinja2) and the agent is a 24/7 process; Vercel is serverless/static and runs neither.
- See [`DEPLOY.md`](DEPLOY.md) for the full Railway walkthrough, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the detailed design.

---

## Sponsor & ecosystem integrations

| Integration | Where | What it does |
|---|---|---|
| **CoinMarketCap** | `brain/cmc_analyzer.py` | structured market metrics (REST, free) + **x402** pay-per-call to the Agent Hub (MCP) |
| **Trust Wallet Agent Kit (TWAK)** | `vault/twak_executor.py` | the only execution layer — agent-side signing, swaps (V2+V3 aggregation), ERC-20 approvals, anti-drain transfers |
| **BNB AI Agent SDK (ERC-8004)** | `boomerang/identity/` | the agent registers a verifiable **on-chain identity** (agentId 131071) — gas-free via MegaFuel |
| **x402** | `boomerang/payments/x402_cmc.py` | real pay-per-call micropayments (EIP-3009 USDC on Base), signed by the BNB AI Agent SDK |
| **Claude (Anthropic)** | `brain/cmc_analyzer.py` | the decision brain (`claude-sonnet-4-6` by default, configurable via `LLM_MODEL`) — forced tool output, deterministic cutoff |

---

## Security model — what the agent never does

- Trade outside the eligible-token whitelist.
- Send funds anywhere except a DEX (swap) or the **owner's personal wallet** (withdraw, pinned with `--confirm-to`).
- Trade with slippage above the cap, a token with a hidden tax, or a desynced price.
- Obey instructions from internet text — the LLM sees **only structured metrics** (anti prompt-injection).
- Accept commands from anyone but the owner (`TELEGRAM_MASTER_USER_ID` pinning).
- Let the AI decide risk — guardrails are deterministic code.

**Self-custody:** the wallet is the agent's own (non-custodial — not held on an exchange). The private key is stored **encrypted** in the agent's runtime environment and never appears in the browser, the site, or the code. Signing happens agent-side; the user-facing layer (bot/site) has no access to the key.

| Threat | Defense |
|---|---|
| Prompt injection | `sanitize_metrics` — numbers/labels only |
| Bot hijack | `TELEGRAM_MASTER_USER_ID` pinning |
| Sandwich / MEV | slippage + `amountOutMin` |
| Hidden tax / honeypot | round-trip retention check |
| Stale oracle ("falling knife") | CMC × pool divergence |
| Loop / gas spam | mutex + cooldown |
| Key theft | encrypted keystore; bot/site have no key access |
| Catastrophic drawdown | deterministic circuit breaker |

---

## Telegram control

The owner drives everything from Telegram:

- `/start` — menu (configure / activate / status / pause / withdraw)
- **3-step config** — focus token · stop-loss (2/4/5%) · take-profit (+5/10/15% or trailing)
- `/status` — live equity, positions, PnL · `/buy <SYMBOL>` — manual buy
- `/pausar` — pause · `/panic` — liquidate everything and halt · "Withdraw All" — return capital to the owner wallet and stop

---

## Getting started (local dev)

```bash
# Python
python -m venv .venv && .venv\Scripts\activate     # Windows (use source on Unix)
pip install -r requirements.txt

# TWAK CLI (self-custody execution + x402) — needs Node 18+
npm install -g @trustwallet/cli                     # the 'twak' CLI
twak wallet create --password "<STRONG_PASSWORD>"   # creates the encrypted keystore

# secrets
copy .env.example .env                              # fill in (see docs/SETUP.md)

# run (paper mode simulates execution, zero risk)
python run_agent.py --paper
python run_agent.py                                 # live (real funds)
```

Required `.env` keys: `ANTHROPIC_API_KEY`, `CMC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_MASTER_USER_ID`, `TWAK_ACCESS_ID`, `TWAK_HMAC_SECRET`, `WALLET_PASSWORD`, `OWNER_WALLET_ADDRESS`. Never commit `.env` (git-ignored).

To run 24/7 hosted (the official instance runs on Railway), see **[`DEPLOY.md`](DEPLOY.md)**.

---

## Project structure

```
run_agent.py                  Local entrypoint (agent + Telegram + dashboard)
railway_start.py              Cloud entrypoint (site + agent, one container)
config.json                   Rules: user (tunable) · dev_safety + hackathon (locked)

boomerang/
  agent.py                    Orchestrator: scan loop, monitor loop, buy/sell/withdraw/panic
  brain/cmc_analyzer.py       Filter 1 — CMC metrics + Claude decision (anti-injection)
  vault/
    bnb_validation.py         Filter 2 — on-chain liquidity/slippage/tax/oracle checks
    twak_executor.py          Filter 3 — TWAK swaps, approvals, transfers
    paper_executor.py         Simulated execution for --paper
  risk/risk_engine.py         Circuit breaker, sizing, trailing, heartbeat, cooldown
  interface/telegram_bot.py   Owner control (InlineKeyboards, MASTER_USER_ID pinning)
  identity/bnb_agent.py       ERC-8004 on-chain identity (BNB AI Agent SDK)
  payments/x402_cmc.py        Real x402 pay-per-call client
  ipc/events.py               Alert bus (agent → Telegram)
  webapp/                     Public site (landing, docs, guide, live proof, demo console)
  persistence.py              State that survives restarts (volume)
```

---

## Configuration (`config.json`)

| Block | Meaning |
|---|---|
| `user` | tunable via Telegram — `token_focus`, `stop_loss_pct`, `take_profit_pct`, `mode` |
| `dev_safety` | locked safety laws — confidence cutoffs (conservative 70 / aggressive 60), slippage cap, drawdown limits, position sizing, min position |
| `hackathon` | event rules — drawdown DQ (30%), min trades/day, eligible-tokens file |

---

## Status

✅ **Live and operating.** Deployed 24/7 on Railway, controlled from Telegram. Full trade lifecycle (buy / sell / withdraw), ERC-8004 identity, and x402 payment are all proven on-chain (see the proof table above).

> **Note on token universe:** the agent trades the focus list via the TWAK aggregator, but on-chain *pricing* (used for stop-loss monitoring and equity) is unreliable for thin-liquidity tokens. The most liquid majors (ETH, ADA, XRP, DOGE, LINK, LTC, AVAX, DOT, UNI, AAVE, BCH) are the recommended set.

## Disclaimer

Operates with **real funds** on BNB mainnet. Real financial risk. This is a research/competition tool, **not financial advice**. No tool guarantees profit — use only what you are willing to risk.

## License

MIT — see [`LICENSE`](LICENSE).
