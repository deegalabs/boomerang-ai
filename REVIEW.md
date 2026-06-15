# Boomerang AI — Security & Honest-State Review

A frank account of **what is real and running**, **what is a showcase**, and **what the
limitations are**. We'd rather you read this than discover it. Positioning: a **personal,
single-owner, self-custody** trading agent — not a multi-tenant platform.

---

## 1. Security model

### Two separate wallets (least privilege)
| | Trade wallet | Identity wallet |
|---|---|---|
| Address | `0xc72a37f4…F67dff` | `0xd06be7Cf…41abc9` |
| Holds funds | **Yes** (the capital) | **No** (gas-free) |
| Job | hold USDC + positions, execute swaps | sign ERC-8004 on-chain proofs |
| In prod | encrypted keystore from `TWAK_WALLET_JSON_B64` + `WALLET_PASSWORD` | encrypted keystore from `IDENTITY_WALLET_JSON_B64` + password |
| Touched by | only the `twak` CLI (local signing) | only the `bnbagent` SDK (metadata writes) |

The money lives **only** in the trade wallet. The identity key is used frequently (a proof
per trade) and lives in a secret, so it is kept **fund-less on purpose**: if it ever leaked,
an attacker could only write fake metadata — **not move a cent**.

### Defenses
- **Self-custody.** The private key never leaves the agent; the web/bot layers never see it.
  Both keystores are **encrypted** and materialized at startup from base64 secrets — nothing
  sensitive is committed or shipped in the image (`.gitignore` / `.dockerignore` / `.railwayignore`).
- **Anti prompt-injection.** The LLM receives **only numeric metrics** (`sanitize_metrics`):
  numbers and short labels pass; any free text / embedded instruction is dropped before it
  reaches the model. The BUY/HOLD action is derived **by code from the score**, not by the
  LLM's word.
- **Risk is deterministic, isolated from the AI.** Circuit breaker, daily loss cap, position
  sizing, cooldown, anti-loop mutex, slippage/oracle/liquidity checks — all in code. An LLM
  hallucination cannot bypass them.
- **Owner-only control.** Telegram commands are pinned to `TELEGRAM_MASTER_USER_ID`; any other
  id is silently ignored. Anti-drain transfers are pinned with `--confirm-to`.
- **Secret hygiene in logs.** The keystore password is **redacted** from the `twak` debug logs;
  a masking filter scrubs private keys / tokens from all logs.

---

## 2. What is REAL and running (mainnet)

- **24/7 autonomous agent** on Railway (site + agent in one container, with supervision +
  liveness restart).
- **Self-custody swaps** on BNB Chain via the Trust Wallet Agent Kit (real buy/sell/withdraw
  proven on-chain — see the README proof table).
- **ERC-8004 on-chain identity** (agentId `131071`) **and live attestation**: the circuit-breaker
  state is written on-chain each cycle, and each trade's reasoning is sealed **before** the
  outcome exists (anti-fabrication). Verifiable on BscScan.
- **Multi-strategy engine** routed by regime: Momentum (uptrend), Mean-Reversion (range),
  DCA (panic) — the deterministic trigger selects, the Opus brain confirms.
- **Capital-protection stack** (deterministic): entry validation → dynamic SL/TP & trailing →
  drawdown circuit breaker (23%) → daily loss cap (15%), plus a depeg guard and an
  **anti-false-trip** guard that skips the cycle on an unreliable equity reading instead of
  liquidating on bad data.
- **Test coverage + CI:** 44 tests over the critical pure logic (risk engine, sanitizer, config,
  strategy router, equity-reliability, log redaction); lint + tests run on every push.

## 3. What is a SHOWCASE (honest)

- **`boomerang/payments/x402_cmc.py`** — a clean, SDK-signed x402 client exercised by
  `scripts/x402_pay.py` for a real end-to-end paid call. It is **not** the runtime data path:
  the brain reads market data over the **CMC REST API**, and runtime x402, when used, goes
  through the `twak x402` CLI.
- **The public demo Console** is **simulated and ephemeral** — a fictional $100 wallet per
  session, in-memory, reset on restart. It reads the agent's real market data but **never
  touches real funds**. Real control is owner-only via Telegram.

## 4. Known limitations (honest)

- **Alpha is unproven.** The capital protection is tested and works; whether the strategies are
  *profitable* has no live track record yet — they are new and unbacktested. Treat funds as
  risk capital.
- **DCA is simplified** (single entry; the 3-order scaling is a future step).
- **Momentum's volume filter is a proxy** (`volume_change_24h_pct`) — the CMC plan exposes no
  hourly volume, so the "1h vol ≥ 15% of 24h" rule is approximated.
- **Thin-liquidity tokens** price unreliably on-chain; the agent trades a curated liquid focus
  list, and the anti-false-trip guard handles transient pricing/RPC gaps.
- **Derivatives funding** is read from a free public source (Binance), since the CMC plan blocks
  the derivatives endpoints.

---

## 5. Threat model

| Threat | Defense |
|---|---|
| Prompt injection | `sanitize_metrics` — numbers/labels only; action derived by code |
| Bot hijack | `TELEGRAM_MASTER_USER_ID` pinning |
| Key theft | encrypted keystores + passwords; web/bot have no key access; password redacted in logs |
| Identity-key leak | identity wallet holds **no funds** (blast radius = fake metadata only) |
| Sandwich / MEV | slippage cap + `amountOutMin` |
| Hidden tax / honeypot | round-trip retention check; curated eligible whitelist |
| Stale oracle / bad RPC | CMC×pool divergence check; **skip-cycle on unreliable equity** (no false liquidation) |
| Stablecoin depeg | deviation guard blocks new entries |
| Catastrophic drawdown | deterministic circuit breaker (23%) + daily loss cap (15%), attested on-chain |
| Loop / gas spam | execution mutex + cooldown |
