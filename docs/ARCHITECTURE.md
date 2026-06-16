# 🪃 Boomerang AI — Architecture

Autonomous short-cycle trading agent on BNB Chain, controlled from Telegram.
Mapped to the project's real files.

---

## 1. Macro view — two layers, one container

```mermaid
%%{init: {'flowchart': {'wrappingWidth': 520}}}%%
flowchart TD
    subgraph USER["👤 USER LAYER · never touches private keys"]
        TG["📱 Telegram Bot — interface/telegram_bot.py<br/>InlineKeyboards · MASTER_USER_ID pinning<br/>/start · /status · /pausar · /panic · /reiniciar · real-time alerts"]
    end

    subgraph AGENT["🔑 AGENT LAYER · holds signing access via TWAK"]
        ORC["🧠 Orchestrator — agent.py · scan_loop (interval) + monitor_loop (2s)"]
        F1["FILTER 1<br/>CMC / Claude"] --> F2["FILTER 2<br/>BNB validate"] --> F3["FILTER 3<br/>TWAK execute"]
        RISK["🛡️ <b>RISK ENGINE</b> · cross-cutting — risk/risk_engine.py<br/>circuit breaker · sizing · trailing · heartbeat · mutex"]
        ORC --> F1
        RISK -. guards .-> F1
        RISK -. guards .-> F2
        RISK -. guards .-> F3
    end

    TG -->|"control intents · configure / start / panic / withdraw"| ORC
    ORC -->|"alerts · AlertBus — ipc/events.py"| TG
    F1 --- CMC(["CoinMarketCap<br/>MCP + x402"])
    F2 --- RPC(["BNB Chain RPC<br/>PancakeSwap"])
    F3 --- TW(["Trust Wallet<br/>Agent Kit · twak"])

    classDef risk fill:#1e293b,stroke:#f3ba2f,stroke-width:1.5px,color:#e2e8f0;
    class RISK risk;
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

```mermaid
%%{init: {'flowchart': {'wrappingWidth': 560}}}%%
flowchart TD
    Start([" each cycle "]) --> A

    A["🛡️ <b>RISK ENGINE</b> · pre-check<br/>equity on-chain → update peak &nbsp;·&nbsp; equity reliable? else SKIP cycle<br/>drawdown ≥23% → PANIC (liquidate + halt) &nbsp;·&nbsp; daily loss ≥15% → halt for the day<br/>heartbeat (&gt;20h without a trade) &nbsp;·&nbsp; can open? cooldown / positions / stable"]
    A -->|ok| B["1️⃣ <b>FILTER 1 · Brain</b> — cmc_analyzer.py<br/>structured CMC metrics (REST/MCP) → SANITIZE anti-injection<br/>→ Claude forced-tool → confidence_score · action &nbsp;·&nbsp; deterministic cutoff: score &lt; min → HOLD"]
    B -->|"BUY · adaptive cutoff ≈58 conservative, floor 52"| C["2️⃣ <b>FILTER 2 · On-chain validation</b> — bnb_validation.py<br/>whitelist · getAmountsOut slippage · round-trip hidden-tax · CMC × pool divergence<br/>all read-only, zero cost"]
    C -->|"approved · min_out computed"| D["3️⃣ <b>FILTER 3 · Execution</b> — twak_executor.py<br/>under a mutex: twak swap USDC→token (agent-side signing)<br/>→ open position with an initial stop-loss · emits a TRADE_OPENED alert"]

    classDef risk fill:#1e293b,stroke:#f3ba2f,stroke-width:1.5px,color:#e2e8f0;
    classDef filter fill:#0b0f19,stroke:#334155,stroke-width:1px,color:#e2e8f0;
    class A risk;
    class B,C,D filter;
```

---

## 2b. The multi-strategy engine (regime-routed)

Filter 1 is not a single rule. `boomerang/strategy/playbook.py` holds **three regime-routed
strategies**; a **deterministic trigger** selects the setup and the Claude brain only
**confirms** it (go/no-go + conviction). Each strategy carries its own exit parameters.

```mermaid
%%{init: {'flowchart': {'wrappingWidth': 460}}}%%
flowchart LR
    T(["deterministic trigger<br/>selects the setup"]) --> M["<b>MOMENTUM</b> · uptrend<br/>1h&gt;+2.5% · 24h&gt;0% · rising volume<br/>SL -1% · trailing 1.5% after +2.5% · 20-min time-stop"]
    T --> R["<b>MEAN-REVERSION</b> · range<br/>1h&lt;-2% dip of a strong token (24h&gt;+4%)<br/>TP +2.5% · SL -0.8% · 120-min time-stop"]
    T --> D["<b>DCA / crisis-rebound</b> · panic<br/>F&amp;G&lt;25 · 24h&lt;-10% · bounce started 1h&gt;+0.5%<br/>TP +3% · no fixed SL (global breaker) · 24h time-stop"]
```

On top of the per-token trigger sit two deterministic governors:

- **Action Matrix** (`regime_posture`): the macro regime dictates *which* strategies may open,
  a **size multiplier** and a **max-positions cap** — RISK_OFF (BTC −5%) stands fully down
  (0.0× / 0 positions); DEFENSIVE shrinks to 0.6× / 2 positions; BULL runs 1.0× / 3.
- **Expectancy arbiter** (`expectancy_disabled`): auto-deactivates any strategy whose recent
  average PnL/trade is negative — even at a high win-rate — once enough trades have closed.

---

## 2c. The TA confluence engine (decides like a human trader)

Wired into Filter 1, before any buy executes, a deterministic **confluence engine** scores the
candidate the way a discretionary trader does — by *confluence* across pillars, **weighted by the
micro-regime** (trend vs range), not by one indicator.

- **`boomerang/strategy/klines.py`** — fetches 1-minute OHLCV from Binance's geo-unblocked public
  host (`data-api.binance.vision`); best-effort (on-chain-only tokens just skip the gate).
- **`boomerang/strategy/indicators.py`** — a pure, unit-tested TA library: EMA-cross, ADX, RSI,
  MACD, Bollinger %B, Z-score, ATR, VWAP, OBV, volume-surge, and **Fibonacci** (golden-pocket
  classification), plus `compute_indicators()` that returns the latest reading of all of them.
- **`boomerang/strategy/confluence.py`** — `evaluate_confluence()` turns those into per-pillar
  votes (trend / momentum / mean-reversion / volume / structure), **weights them by regime**,
  applies **hard vetoes** (never chase a vertical pump), and yields a decision (ENTER / WAIT /
  AVOID), a 0–100 score, and a **human-readable checklist**.

In `agent.run_cycle`, the gate **vetoes** AVOID candidates, **scales conviction** by the score, and
folds the confluence summary into the on-chain `commit_prediction`. The action is still derived **by
code**; the LLM only confirms the narrative. The public `/live` page renders the same analysis live —
an annotated candle chart (EMA · VWAP · Fibonacci golden pocket) + the confluence panel — and the
demo Console runs the identical engine on a simulated $100 bankroll.

---

## 3. The exit monitor (stop / trailing)

A parallel 2s loop (`agent.check_positions`), leveraging BSC's fast blocks:

```mermaid
%%{init: {'flowchart': {'wrappingWidth': 480}}}%%
flowchart TD
    P["<b>for each open position</b><br/>price = bnb_validation.onchain_price_usd(token) · read-only, no gas<br/>signal = risk.evaluate_position(position, price)"] --> S{"signal?"}
    S -->|HOLD| K["keep"]
    S -->|SELL_STOP_LOSS| X1["dropped past the stop → sell"]
    S -->|SELL_TRAILING| X2["rose, locked break-even, followed the peak,<br/>then pulled back → sell IN PROFIT"]
    S -->|SELL_TAKE_PROFIT| X3["hit the target → realize the gain"]
    X1 --> SW["twak swap token→USDC<br/>TRADE_CLOSED alert (with PnL)"]
    X2 --> SW
    X3 --> SW
```

---

## 4. The flow of money (the "boomerang")

```mermaid
flowchart LR
    PW(["💼 Personal Wallet"]) -->|deposit bankroll| AW(["🤖 Agent Wallet"])
    AW -->|trades| PS(["🥞 PancakeSwap"])
    AW -.->|"/panic · twak transfer --confirm-to<br/>(converts to stable, sends it back)"| PW
```

- **Competition mode:** trades continuously, compounding the bankroll.
- **Boomerang (automatic return at cycle end):** a future/demo enhancement.
- **`--confirm-to`** pins the withdrawal destination = anti-drain shield.

**Two wallets, by design:**
- **Trade wallet** `0xc72a37f4bb7c454Fd8a9EB629aFaEeb101F67dff` — holds the funds and executes
  the swaps via twak. All the money lives here.
- **Identity wallet** `0xd06be7Cf5D097F13Dbf6C35943616EC21641abc9` — a **separate, fund-less**
  wallet that only signs the ERC-8004 on-chain proofs. It never touches custody, so attestation
  can never put the bankroll at risk.

---

## 5. The two layers of rules

| DEV layer (immutable, in code)             | USER layer (via Telegram)            |
|--------------------------------------------|--------------------------------------|
| eligible-token whitelist                   | token focus (liquid subset)          |
| global drawdown circuit breaker (23%/DQ 30%) | stop-loss (2% / 4% / 5%)           |
| daily loss cap (15% intraday)              | mode (conservative ≈58 / aggressive ≈52, adaptive, floor 52) |
| slippage cap (1.5%)                        | (per-trade size = % of equity, base 10%) |
| stablecoin depeg guard · dynamic SL/TP · trailing · time-stop | |
| anti-false-trip (skip on bad equity read) ·  destination lock (anti-drain) | |
| min trades / heartbeat · cooldown · anti-loop mutex | |

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
| Stablecoin depeg                | depeg guard on the base stable (risk_engine)       |
| Bad equity read (RPC/price glitch) | anti-false-trip: SKIP the cycle instead of liquidating on bad data (risk_engine) |
| Key theft                       | encrypted keystore in twak; bot/site have no access; **identity wallet is fund-less** |
| Host exposure (cloud)           | secrets as protected env vars; never in repo/image; small bankroll bounds risk |
| Catastrophic drawdown / DQ      | deterministic circuit breaker (23%) + 15% intraday daily loss cap (risk_engine) |
| Fabricated reasoning / hindsight | on-chain attestation: commit_prediction seals the reasoning + falsifier BEFORE the outcome (identity/bnb_agent) |

---

## 7. Mapping to sponsors and prizes

- **CoinMarketCap (Agent Hub):** Filter 1 consumes data via REST/MCP. **x402 is now
  load-bearing in the trade loop** (not just a showcase): the agent pays a real USDC-on-Base
  micropayment (~$0.01, EIP-3009, ~1×/hour) for Agent Hub derivatives the REST plan blocks,
  and that data feeds the brain (best-effort, with a Binance-funding fallback)
  → competes for "Best Use of Agent Hub".
- **Trust Wallet (TWAK):** the single execution layer, multiple surfaces (signing +
  autonomous mode + x402 micropayments), self-custody → targets the "Best Use of TWAK" rubric.
- **BNB AI Agent SDK:** the agent's on-chain identity (ERC-8004, agentId 131071, gas-free via
  MegaFuel) **plus live attestation** — `commit_prediction` (seals each trade's reasoning +
  falsifier before the outcome), `publish_track_record`, and `publish_risk_state` (circuit-breaker
  state each cycle), signed by the fund-less identity wallet → "Best Use of BNB SDK".
- **Track 1 (PnL):** deterministic guardrails maximize return without breaching the
  drawdown that disqualifies.
