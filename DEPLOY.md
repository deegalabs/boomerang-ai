# Deploy — Boomerang AI

> **Recommended path: Railway** (below). The VPS/systemd section follows as an alternative.

## Railway (official deployment)

Real architecture: **a single Railway service** runs the **public site + the 24/7 agent** in the same container ([`Dockerfile`](Dockerfile): Python 3.12 + Node 20 + the TWAK CLI). The entrypoint [`railway_start.py`](railway_start.py) starts the **site** (uvicorn, main thread, answers `/healthz` immediately) and the **agent** (own thread), sharing a **volume** (`/app/state`) for state, so `/live` shows real trades. It is the **same trading wallet**: the **encrypted** keystore (`~/.twak/wallet.json`) and the initial state are materialized at boot from base64 env vars — no fund migration, no raw key.

> Why not Vercel: the site is server-rendered Python (Starlette + Jinja2) and the agent is a 24/7 process. Vercel is serverless/static and runs neither. Hence, everything on Railway.

```bash
# 1. CLI + login
npm i -g @railway/cli
railway login

# 2. project + state volume (at the repo root)
railway init                          # create the project
railway volume add -m /app/state      # positions survive restarts

# 3. variables: migrate the .env secrets + the encrypted keystore + state,
#    WITHOUT printing values (generates TWAK_WALLET_JSON_B64 and STATE_SEED_B64 as base64)
python scripts/railway_setvars.py
railway variables --set "SESSION_SECRET=$(python -c 'import secrets;print(secrets.token_hex(32))')"
railway variables --set "OWNER_WALLET_ADDRESS=0x...your_personal_wallet..."

# 4. build/deploy + public URL
railway up --detach
railway domain
```

[`railway.json`](railway.json) uses the Dockerfile builder, start `python railway_start.py`, and healthcheck `/healthz`. [`.dockerignore`](.dockerignore) ensures `.env`, `identity_wallet/`, and `state/` **never** enter the image — secrets arrive only as protected Railway environment variables, at runtime.

**Verify** (`railway logs -d`): you should see `Equity inicial (on-chain)`, `Estado restaurado`, `Identidade ERC-8004`, `Bot do Telegram em polling`, and the `CICLO` lines. The public `/api/live` shows real `IN_POSITION`/equity/holdings.

> **Security (cloud):** the encrypted keystore **and** `WALLET_PASSWORD` both live on Railway, so the key is decryptable on the provider. This is inherent to any hosted bot; a small bankroll bounds the risk. Secrets stay only in env vars (never in the repo/image). **Rotate `TELEGRAM_BOT_TOKEN`** (via @BotFather) if it ever appeared in a log before the logging filter was added.

### Real x402

`twak` now runs **inside the container** (same image). The site's own `/x402` route injects the header that CoinMarketCap's MCP requires, so the pay-per-call payment can be settled from the cloud or from a local machine pointing at the public URL:

```bash
twak x402 request --method POST \
  --body '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_global_metrics_latest","arguments":{}}}' \
  --max-payment 10000 --prefer-network base --prefer-method eip3009 --yes \
  https://YOUR-APP.up.railway.app/x402
```

A 200 with data = payment settled on-chain ($0.01 USDC on Base). In normal operation, data comes via **free REST** (do not set `X402_ENDPOINT`); real x402 is a proof/demo, not a per-cycle cost.

> **Do not run a local agent** alongside the Railway one: two agents on the same wallet conflict.

---

## VPS / systemd (alternative)

How to run, on a Linux VPS, the three Boomerang AI processes:

1. **Agent** (`run_agent.py`) — trades 24/7, controlled from Telegram.
2. **Public site** (`boomerang.webapp.site:app`) — landing, docs, guide, live proof, demo console.
3. **x402 proxy** (`boomerang.webapp.x402_proxy`) — gives `twak x402` the public endpoint it requires (unlocks real pay-per-call to CoinMarketCap).

The ERC-8004 on-chain identity (agentId **131071**) is already registered on BNB mainnet; nothing to do here beyond versioning `boomerang/identity/agent_card.json` (already done).

---

## 1. Prerequisites

- Linux VPS (Ubuntu 22.04+), a domain pointing to the IP (e.g. `boomerang.deegalabs.ai`).
- Python 3.12, Node 20+ (for `twak`), `git`, and a TLS reverse proxy (**Caddy** recommended for simplicity).
- Ports 80/443 open.

## 2. Code, dependencies, and secrets

```bash
git clone https://github.com/deegalabs/boomerang-ai.git
cd boomerang-ai
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# TWAK CLI (self-custody + x402). Install locally, as on the dev machine:
corepack pnpm add @trustwallet/cli   # produces ./node_modules/.bin/twak

cp .env.example .env   # then fill it in (NEVER version .env)
```

In `.env` (real values, outside git):

```ini
ANTHROPIC_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_MASTER_USER_ID=...
CMC_API_KEY=...
TWAK_ACCESS_ID=...
TWAK_HMAC_SECRET=...
WALLET_PASSWORD=...
OWNER_WALLET_ADDRESS=0x779126dd2937974118d67568d1bc5b69b84f059c
TWAK_BIN=/srv/boomerang-ai/node_modules/.bin/twak
# ERC-8004 identity keystore password (falls back to WALLET_PASSWORD if absent):
# BNB_IDENTITY_PASSWORD=...
# Public x402 endpoint (see step 5) — unlocks real payment via twak:
# X402_ENDPOINT=https://boomerang.deegalabs.ai/x402
DASHBOARD_TOKEN=...        # optional, read-only agent dashboard (port 8080)
```

> The identity wallet (`identity_wallet/`) and `.env` are git-ignored. To migrate the identity from the dev machine, copy the `identity_wallet/` folder over a secure channel (it contains the key). Otherwise, run `python scripts/register_identity.py` on the VPS to generate a fresh one.

## 3. Public site (systemd)

`/etc/systemd/system/boomerang-site.service`:

```ini
[Unit]
Description=Boomerang AI site
After=network.target

[Service]
WorkingDirectory=/srv/boomerang-ai
EnvironmentFile=/srv/boomerang-ai/.env
ExecStart=/srv/boomerang-ai/.venv/bin/uvicorn boomerang.webapp.site:app --host 127.0.0.1 --port 8090
Restart=always

[Install]
WantedBy=multi-user.target
```

## 4. Agent (systemd)

`/etc/systemd/system/boomerang-agent.service`:

```ini
[Unit]
Description=Boomerang AI agent
After=network.target

[Service]
WorkingDirectory=/srv/boomerang-ai
EnvironmentFile=/srv/boomerang-ai/.env
ExecStart=/srv/boomerang-ai/.venv/bin/python run_agent.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## 5. Real x402 (proxy + Caddy)

`twak x402` rejects private/loopback addresses — it needs a **public endpoint with TLS**. The proxy runs on loopback and Caddy exposes `/x402` publicly (the proxy injects the MCP `Accept` header).

`/etc/systemd/system/boomerang-x402.service`:

```ini
[Unit]
Description=Boomerang AI x402 proxy
After=network.target

[Service]
WorkingDirectory=/srv/boomerang-ai
EnvironmentFile=/srv/boomerang-ai/.env
ExecStart=/srv/boomerang-ai/.venv/bin/python -m boomerang.webapp.x402_proxy
Restart=always

[Install]
WantedBy=multi-user.target
```

Then point the agent at the public endpoint in `.env`:

```ini
X402_ENDPOINT=https://boomerang.deegalabs.ai/x402
```

With that, `twak x402 request` pays the CMC calls ($0.01 each) with the **USDC already in the trading wallet** — no new funds, no ETH, no key exposure.

## 6. Caddy (TLS + routes)

`/etc/caddy/Caddyfile`:

```
boomerang.deegalabs.ai {
    # x402: public path twak calls -> local proxy -> CMC
    handle_path /x402* {
        reverse_proxy 127.0.0.1:8402
    }
    # public site
    reverse_proxy 127.0.0.1:8090
}
```

Caddy provisions the Let's Encrypt certificate automatically.

## 7. Bring up and verify

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now boomerang-x402 boomerang-site boomerang-agent caddy

# site health + identity
curl -s https://boomerang.deegalabs.ai/healthz

# real x402 (one paid $0.01 call from the trading wallet):
. .venv/bin/activate
twak x402 request --method POST \
  --body '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_global_metrics_latest","arguments":{}}}' \
  --max-payment 10000 --prefer-network base --prefer-method eip3009 --yes \
  https://boomerang.deegalabs.ai/x402
```

A 200 with data = x402 payment settled on-chain. From there, the agent can use paid CMC data by setting `X402_ENDPOINT` (otherwise it stays on free REST, the zero-cost default).

## Alternative: x402 via the identity wallet (no twak)

The `boomerang/payments/x402_cmc.py` client signs the payment directly with the BNB AI Agent SDK (no proxy, no public address needed). It just requires the **identity wallet** (`0xd06be7…`) to hold USDC on Base. For a proof call:

```bash
python scripts/x402_pay.py            # pays $0.01 and prints the data
```

Useful if you prefer to fund the identity wallet (e.g. an exchange withdrawal, which covers gas) instead of using twak on a VPS.
