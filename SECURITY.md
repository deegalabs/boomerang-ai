# Security Policy

Boomerang AI is a **personal, single-owner, self-custody** autonomous trading agent.
This document is the result of a full-repository security audit: the threat model, the
defenses in place, the open findings with severities, and how to report a vulnerability.

> **Scope of trust.** There is exactly **one** privileged user (the owner, pinned by
> `TELEGRAM_MASTER_USER_ID`). The public web Console is a **simulated demo** isolated from
> real funds. This is not a multi-tenant platform, and the security model is calibrated to
> that: protect the keys and the capital, keep the AI from being able to do harm, and keep
> the public surface incapable of touching money.

---

## 1. Reporting a vulnerability

If you find a security issue, please **do not open a public issue**. Report it privately:

- Open a [GitHub Security Advisory](../../security/advisories/new) (preferred), or
- Contact the owner directly through the repository profile.

Please include: a description, reproduction steps, the impact you believe it has, and any
PoC. We aim to acknowledge within a few days. As a hackathon / personal project there is no
bug-bounty, but credit is gladly given.

**Out of scope:** the public demo Console (simulated, no real funds), denial-of-service via
volumetric flooding of the public site, and findings that require the owner's own machine
or secrets to already be compromised.

---

## 2. Asset & trust model

| Asset | Where it lives | Who can touch it |
|---|---|---|
| **Trade wallet key** (holds the capital) | encrypted keystore, materialized at startup from `TWAK_WALLET_JSON_B64` + `WALLET_PASSWORD` | only the `twak` CLI (local signing) |
| **Identity wallet key** (signs ERC-8004 proofs) | encrypted keystore from `IDENTITY_WALLET_JSON_B64` + password | only the `bnbagent` SDK |
| **Capital** (USDC + positions) | the trade wallet **only** | the agent's deterministic execution path |
| **Agent control** | Telegram | a single id (`TELEGRAM_MASTER_USER_ID`) |

**Two-wallet least privilege.** The money lives **only** in the trade wallet. The identity
key is used often (a proof per trade) and lives in a secret, so it is deliberately kept
**fund-less**: if it ever leaked, the blast radius is *fake on-chain metadata* — **not a
single cent moved**.

---

## 3. Defenses (verified in this audit)

### Secrets & supply chain
- **No secret has ever been committed.** Verified across the full git history (`git log`
  over `.env`, keystores, `identity_wallet/`, `state/` — all clean, never tracked).
- **No hardcoded secrets** in source. The only key-like string in the tree is a **public**
  transaction hash in `agent_card.json` (on-chain, by design).
- **Layered ignore coverage:** `.gitignore` / `.dockerignore` / `.railwayignore` all exclude
  `.env`, `identity_wallet/`, `state/`, `~/.twak/`, `.venv/`, `logs/`, `secrets/`, `wallets/`.
  Nothing sensitive is committed *or* shipped in the container image.
- **Encrypted keystores at rest.** Both wallets are AES-encrypted keystores, materialized at
  startup from base64 env secrets — the plaintext key never lands on disk in the repo or image.
- **Pinned dependencies.** `requirements.lock` (76 packages, fully pinned `==`) and pinned
  `requirements.txt` — reproducible builds, no surprise transitive upgrades.

### Code execution
- **No dynamic execution.** No `eval`, `exec`, `os.system`, `shell=True`, or `pickle.loads`
  on untrusted input anywhere in the tree.
- **No shell injection.** The single subprocess boundary (`twak_executor.py`) invokes
  `subprocess.run(cmd, ...)` with `cmd` as an **argument list** — arguments are passed
  directly to the binary, never through a shell.

### The AI cannot do harm
- **Anti prompt-injection (`sanitize_metrics`).** The LLM receives **only numeric metrics**
  and short labels; a regex (`_INJECTION_PATTERNS`) strips embedded instructions and caps
  label length. Free text from market feeds never reaches the model as instructions.
- **Action is derived by code, not by the model's words.** BUY/HOLD/SELL comes from the
  numeric score and the deterministic risk engine. A hallucinated or injected "ignore your
  rules and sell everything" has no execution path.
- **Risk is deterministic and isolated from the AI.** Circuit breaker (23% drawdown), daily
  loss cap (15%), position sizing, cooldown, anti-loop mutex, slippage / oracle-divergence /
  liquidity / depeg checks — all in pure, tested code (`risk/risk_engine.py`). An LLM cannot
  bypass them.

### Wallet & key handling
- **Self-custody.** The private key never leaves the agent; the web and bot layers never see it.
- **Owner-only control.** Telegram commands are pinned to `TELEGRAM_MASTER_USER_ID`; any other
  id is silently ignored.
- **Anti-drain transfers.** Withdrawals are pinned with `--confirm-to`, so a swap path cannot
  be redirected to an arbitrary address.
- **Password redaction in logs.** `_redact_args` scrubs the value after `--password` and drops
  `0x…` material from the `twak` debug logs; a masking filter scrubs keys/tokens from all logs.

### Web surface
- **Sessions are signed, comparisons are timing-safe.** Authenticated sessions use HMAC-SHA256
  over a `SESSION_SECRET`, verified with `hmac.compare_digest` (constant-time). SIWE
  (Sign-In-With-Ethereum) signatures are verified by on-chain address recovery (`_recovered_ok`).
- **The demo is sandboxed.** The public Console runs a **fictional $100 in-memory wallet** per
  session (random `secrets.token_hex(20)` address), reset on restart. It reads real market data
  but **never touches real funds**. Public read endpoints (`/api/live`) expose only the
  already-public persisted agent state — no keys, no balances of the real wallet beyond what is
  intentionally published as on-chain proof.
- **No SSRF in the x402 proxy.** The `/x402` relay forwards to a **fixed** upstream
  (`_X402_TARGET`, CMC's MCP); the caller cannot redirect the target.

---

## 4. Open findings

None are remotely exploitable for fund loss given the single-owner, fund-isolated model.
Tracked here for honesty and follow-up.

| # | Finding | Severity | Status | Notes / mitigation |
|---|---|---|---|---|
| F-1 | **Keystore password passed via `--password` argv** to `twak`. Visible in `/proc/<pid>/cmdline` to a local process on the same host. | 🟠 Medium | Open | Not shell-injectable and redacted from *our* logs. Real exposure requires already having code-exec on the container. Fix is blocked on verifying whether `twak` accepts the password via env/stdin; will switch if so. |
| F-2 | **Dashboard auth via `?key=` query token** (`server.py`). Tokens in query strings can leak to proxy/access logs and `Referer`. | 🟡 Low | ✅ Resolved | The legacy local dashboard (`server.py` + `dashboard.html`) was **removed**. The read-only panel is now the public, token-less `/live` page (`site.py`); the Telegram `/dashboard` command points there. No `?key=` surface remains. |
| F-3 | **`/x402` proxy is an unauthenticated open relay.** No auth or rate-limit on the endpoint. | 🟡 Low | Accepted | Fixed upstream (no SSRF) and the upstream **requires a 402 USDC payment**, so abuse is bandwidth-only, not free API access. Mitigation if needed: a shared-secret header or IP allowlist. |
| F-4 | **Demo Console entry is unauthenticated** (`auth_guest`). Anyone can start a simulated session. | 🟢 Info | By design | The demo is simulated and isolated from funds; open entry is intentional for judges/visitors. |
| F-5 | **`SESSION_SECRET` defaults to a random per-restart value** if unset. Sessions invalidate on each restart. | 🟢 Info | By design | Secure default (random 32 bytes); set the env var for session persistence across restarts. |

---

## 5. Threat model

| Threat | Defense |
|---|---|
| Prompt injection / hostile market text | `sanitize_metrics` (numbers + short labels only); action derived by code from the score |
| Bot hijack | `TELEGRAM_MASTER_USER_ID` pinning; non-owner ids silently ignored |
| Key theft from repo/image | encrypted keystores materialized from base64 secrets; never committed or shipped |
| Key theft from logs | `_redact_args` + masking filter scrub passwords/keys/tokens |
| Identity-key leak | identity wallet holds **no funds** → blast radius = fake metadata only |
| Shell / code injection | no `eval`/`exec`/`os.system`/`shell=True`; subprocess uses arg lists |
| SSRF via proxy | `/x402` forwards to a fixed upstream only |
| Drain via redirected withdrawal | `--confirm-to` pins the destination |
| Session forgery / timing attack | HMAC-signed sessions + `hmac.compare_digest`; SIWE address recovery |
| Sandwich / MEV | slippage cap + `amountOutMin` |
| Honeypot / hidden tax token | round-trip retention check + curated liquid whitelist |
| Stale oracle / bad RPC | CMC×pool divergence check; **skip-cycle on unreliable equity** (no false liquidation) |
| Stablecoin depeg | deviation guard blocks new entries |
| Catastrophic drawdown | deterministic circuit breaker (23%) + daily loss cap (15%), attested on-chain |
| Loop / gas spam | execution mutex + cooldown |
| Supply-chain drift | fully pinned `requirements.lock` |

---

## 6. Audit summary

A full-repository review (secrets, history, dynamic execution, injection, wallet handling,
web surface, dependencies) found **no critical or high-severity issues** and **no secret
exposure**. The architecture's core security properties — self-custody, two-wallet least
privilege, AI-isolated deterministic risk, and a fund-isolated public demo — hold up. The
open findings above are low-to-medium and bounded by the single-owner trust model.

_Last audited: 2026-06-14._
