# 🗺️ Boomerang AI — Roadmap (additive improvements)

Everything here is **additive** — it strengthens what's already built and shipped, without changing
existing behavior. Each item maps to a **BNB Hack pillar** (Track 1 PnL · Best Use of TWAK · Best Use
of CMC/Agent Hub x402 · Best Use of BNB SDK/ERC-8004). Derived from a deep-dive of canonical
implementations (ChaosChain ERC-8004, Google a2a-x402, agentic-wallet-guard, BinacciAI). **Status:
proposed — pending approval before execution.**

> Principle for every item: ships behind a flag/config when sensible · cycle is
> `branch → implement → ruff + pytest → deploy → verify` · DoD = lint clean, all tests (94 + new)
> green, `/live` and demo intact, deploy confirmed.

---

## Phase A — zero-risk, high narrative (do first)

### A1 · Security hardening *(strengthens: TWAK self-custody · Track 1 anti-DQ)*
Defense-in-depth on top of `sanitize_metrics` + the risk engine (does **not** touch them).
- `boomerang/risk/integrity.py` *(new)* — `sign_state` / `verify_state` (HMAC-SHA256, `STATE_HMAC_SECRET`).
- `persistence.py` *(additive)* — write a `.sig` on save; verify on load; mismatch → don't trade + alert (reuse skip-cycle), never run on tampered state. Graceful first-run (no sig → accept + write).
- `risk_engine.py` *(additive)* — `too_many_buys(now, window=60, limit=3)` anomaly guard; in `run_cycle`, trip → `halt()` + alert (catches prompt-injection cascades).
- `logs/audit.jsonl` *(new)* — structured rejection log (reason codes).
- `SECURITY.md` *(additive)* — document the new layers.
- **Tests:** `test_integrity.py`, `risk_engine` (+anomaly). **Risk:** very low (only ever *blocks*). **Effort:** ~½ day.

### A2 · Decision-trace (why it did NOT enter) *(strengthens: transparency · Track 1 · the demo)*
Structure the "no entry" reasoning and surface it.
- `agent.py` *(additive)* — collect `self._last_traces` (cap ~20): `{symbol, blocked_at: REGIME|BRAIN|CONFLUENCE|VALIDATION, reason}`.
- `snapshot()` / `/api/live` *(additive)* — expose `traces`.
- `live.html` / `console.html` *(additive)* — a "Decision trace" panel/modal.
- **Tests:** render check. **Risk:** zero (observability). **Effort:** ~½ day.

---

## Phase B — sponsor prizes (additive)

### B1 · ERC-8004 — add the Reputation Registry channel *(strengthens: Best Use of BNB SDK)*
We seal via `set_metadata` today; the canonical spec also uses the **Reputation Registry**
(`giveFeedback`, aggregatable on-chain). **Additive:** keep metadata AND, after a trade closes,
publish a signed feedback.
- **Step 0 (dependency to verify):** is the Reputation Registry deployed on BNB Chain? If **yes** → use it; if **no** → publish the feedback as an on-chain **hash** via our existing channel + a public proof JSON (still additive, shows spec awareness); deploying the reference contract (gas-free via MegaFuel) is an optional bigger path.
- `identity/bnb_agent.py` *(new method)* — `publish_reputation(yield_bps, uri, tag="tradingYield")`.
- `agent.py` `_sell/_close` *(additive, non-blocking)* — fire after a trade settles (like `commit_prediction`).
- Proof doc served from our own URL + hash (avoids an IPFS hard dependency).
- **Tests:** `test_reputation_payload` (pure). **Risk:** medium — gated on Step 0. **Effort:** ~1 day.

### B2 · x402 — align to the Google standard + (optional) self-hosted facilitator *(strengthens: Best Use of Agent Hub)*
- `boomerang/payments/x402_std.py` *(new)* — map our payment result → standard `PaymentRequirements/PaymentPayload/receipt` (vendor the `x402_a2a` constants to avoid a new dep).
- `/api/x402-status` *(new, read-only)* — expose the last receipt in standard format (proof of conformance).
- *(Phase 2, optional)* self-host `qntx/facilitator` and point twak/proxy at it → x402 fully ours.
- **Tests:** `test_x402_std` (pure mapping). **Risk:** low. **Effort:** ~½ day (wrapper); facilitator +1–2 days.

---

## Phase C — protect the PnL

### C1 · Fee-aware entry gate *(strengthens: Track 1 PnL)*
- `vault/bnb_validation.py` `validate()` *(additive)* — `roundtrip = pool_fee*2 + est_gas_usd/amount + slippage`; if `roundtrip ≥ min_edge` → `Rejected("fee > edge")`.
- `config.json` *(additive)* — `min_edge_over_fees_pct` (start permissive).
- **Tests:** `test_fee_gate` (pure). **Risk:** low (only blocks bad entries). **Effort:** ~½ day.

---

## Strategy expansion — the 9 Binacci strategies (additive)

Our `playbook.py` already has the `StrategySpec` abstraction (deterministic trigger + exit params)
plugged into the Action Matrix + confluence. So **adding strategies = new specs, no engine rewrite.**

### Prerequisite — a lightweight backtest harness *(strengthens: decision quality / Track 1)*
We don't have one (Binacci does). Without it, adding strategies is guesswork (overfitting risk).
- `boomerang/strategy/backtest.py` *(new)* — replays historical Binance klines through our existing
  `indicators.py` + a spec's trigger/exit, reporting expectancy / win-rate / max-DD. **Causal**
  (no lookahead). Reuses everything we have; valuable on its own. **Effort:** ~1–1.5 days.

### The 9, classified for our spot-only, self-custody context
| Strategy | Fit | How |
|---|---|---|
| Momentum Breakout (Donchian + retest) | ✅ additive | new `StrategySpec`, reuses our indicators |
| Trend Follow (EMA stack) | ✅ additive | EMA/ADX already in `indicators.py` |
| Volatility Squeeze (BB bandwidth) | ✅ additive | Bollinger already there |
| VWAP Reversion | ✅ additive | VWAP already there |
| Liquidity Sweep (wick reclaim) | ✅ additive | reuses swing / Fibonacci |
| Mean Reversion (RSI/BB) | ⚠️ have it | refine, don't duplicate |
| Reaction (core 5-gate) | ⚠️ overlap | ≈ our existing pipeline skeleton |
| Funding Carry (perp) | ❌ out of scope | needs **perps** (we are spot-only) |
| Basis Carry (spot-perp) | ❌ out of scope | needs perps |

### Adoption plan (additive, validated)
1. Build the **backtest harness** (prerequisite above).
2. Add **2–3 high-value spot strategies** — **Trend Follow, Volatility Squeeze, VWAP Reversion** —
   each a new `StrategySpec` + deterministic trigger (reusing `indicators.py`).
3. **Validate each on backtest/paper**; only enable in the Action Matrix if expectancy is positive
   (the existing **expectancy arbiter** is the live safety net).
4. **Confluence on top** (already built) confirms every entry — so new strategies pass the same
   quality gate.
- **Risk:** moderate — bounded by the backtest gate + the arbiter. **Out of scope:** perps
  (Funding/Basis Carry). **More strategies ≠ better without validation** — that's why the harness comes first.

---

## Future roadmap (post-hackathon) — Multi-agent / SaaS

Today: **1 agent · 1 wallet · 1 owner.** The product vision is a **SaaS for other users**, which is a
large architectural shift (correctly deferred):
- **Multi-tenancy** — an isolated agent instance per user (own keystore, config, state, wallet);
  per-tenant security isolation.
- **Orchestrator** — manages N agent processes with **per-agent x402 budgets** (BlockRun's
  `delegate` pattern) and independent limits.
- **Platform** — auth, billing, per-user dashboards, wallet provisioning at scale.
- Deferred because it needs key-management at scale, isolation, infra and billing — none of which the
  hackathon asks for, and it would touch what already works.

---

## Out of context — deliberately skipped (would drift)
- **Replacing CMC with BlockRun/Surf** → weakens "Best Use of CMC Agent Hub".
- **Polymarket / X-Twitter data** → new scope + prompt-injection risk (their X data is raw text).
- **Migrating ALL of ERC-8004 to Reputation/Validation** (vs adding) → would change what works.
- **Perp strategies / 9-strategy full engine rewrite** → outside "improve what's done".
