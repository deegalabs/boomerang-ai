# 🔑 Setup — credentials & wallet (step by step)

The code is ready. This is the part **only you** can do: obtain credentials, create/fund
the wallet, and populate the eligible tokens. Do it in the order below (easiest to most
technical).

> ⚠️ Open a **fresh terminal** first, so `python`, `node`, and `twak` are on the PATH.
> Test: `python --version`, `node --version`, `twak --version`.
> Never paste a private key/seed into a chat. Never commit `.env`.

---

## 1. Telegram bot (5 min, free) ✅ easiest

1. In Telegram, open **@BotFather** → `/newbot` → give it a name and a @username.
2. It returns a **token** like `8123456789:AAH...`. Save it → goes in `TELEGRAM_BOT_TOKEN`.
3. Find your **user ID**: message **@userinfobot** (it replies with your `Id`).
   That number goes in `TELEGRAM_MASTER_USER_ID` (the lock that lets only YOU command it).

## 2. Claude API (Anthropic) — the "brain" (5 min)

1. Go to **console.anthropic.com** → create account / log in.
2. **API Keys** → *Create Key* → copy it (starts with `sk-ant-...`).
3. Goes in `ANTHROPIC_API_KEY`. (For dev, the cost is minimal.)

## 3. CoinMarketCap — the "eyes" (10 min)

1. Go to **coinmarketcap.com/api** → create a developer account → get the **API Key**.
2. See the **AI Agent Hub**: **coinmarketcap.com/api/agent** (MCP + x402).
3. Put the key in `CMC_API_KEY`.
   - *Optional (x402 prize):* pay-per-call uses **USDC on Base**. You can defer this — in
     dev the REST API key is enough. The real x402 client is in `boomerang/payments/`.

## 4. Trust Wallet Agent Kit (TWAK) — the "hands/vault" (15 min)

The simplest path is the interactive onboarding (run it **in your terminal**):

```bash
twak setup
```

It guides you through: (a) credentials, (b) wiring, (c) wallet creation. Manual alternative:

1. Go to **portal.trustwallet.com** → create an app → generate an **API key**.
2. Copy **Access ID** and **HMAC Secret** (the secret is shown ONCE).
3. In the terminal:
   ```bash
   twak init --api-key <ACCESS_ID> --api-secret <HMAC_SECRET>
   ```
   (saves to `~/.twak/credentials.json`)
4. Also put them in `.env`: `TWAK_ACCESS_ID` and `TWAK_HMAC_SECRET`.

## 5. Create and fund the agent wallet (15 min) 💰

1. Create the agent wallet (self-custody — the seed lives in an **encrypted** twak keystore,
   protected by the password):
   ```bash
   twak wallet create --password "A_STRONG_PASSWORD"
   ```
   → Save the password in `WALLET_PASSWORD`. **Write down the seed** somewhere safe (offline).
   In a hosted deployment, that encrypted keystore goes to the provider as an environment
   variable (see `DEPLOY.md`); so does the password. The key never appears in the
   site/browser/code.
2. Get the agent address on BSC:
   ```bash
   twak wallet address --chain bsc
   ```
3. From your **personal Trust Wallet** app (phone), send over the **BNB Smart Chain (BEP-20)**
   network:
   - A **small test bankroll** in **USDC** (e.g. $20–50). USDC is the agent's base trading stable.
   - A bit of **BNB** for gas (e.g. $2–3).
4. Check the balance:
   ```bash
   twak wallet portfolio --chains bsc --password "A_STRONG_PASSWORD"
   ```
5. In `OWNER_WALLET_ADDRESS`, put your **personal wallet** address
   (the withdrawal / boomerang destination).

> 💡 You keep full control: by importing the agent wallet's seed into the Trust Wallet app,
> you can withdraw manually at any time.

## 6. Populate the eligible tokens (`data/eligible_tokens.json`)

The agent only trades eligible tokens. To **start**, the **liquid subset** (about 12) is
enough — that is what the conservative strategy uses.

**How to get each address safely:** on the token's CoinMarketCap page, the **Contracts**
section, copy the **BNB Smart Chain (BEP-20)** address and verify it on **bscscan.com**.
File format:

```json
{
  "base": { "USDT": "0x55d398326f99059fF775485246999027B3197955" },
  "tokens": {
    "ETH": "0x...",  "XRP": "0x...", "DOGE": "0x...", "ADA": "0x...",
    "LINK": "0x...", "LTC": "0x...", "AVAX": "0x...", "DOT": "0x...",
    "UNI": "0x...",  "AAVE": "0x...","ATOM": "0x...","BCH": "0x..."
  }
}
```

> Prefer the most liquid majors (ETH, ADA, XRP, DOGE, LINK, LTC, AVAX, DOT, UNI, AAVE, BCH).
> The agent trades thin-liquidity tokens via the TWAK aggregator, but on-chain *pricing*
> (used for stop-loss monitoring) is unreliable for them.

## 7. Assemble the `.env`

```bash
cp .env.example .env   # (copy on Windows)
```
Fill in: `WALLET_PASSWORD`, `OWNER_WALLET_ADDRESS`, `TWAK_ACCESS_ID`,
`TWAK_HMAC_SECRET`, `CMC_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_MASTER_USER_ID`. (On Windows, also set `TWAK_BIN` and `NODE_DIR` if `twak`
is not on the PATH — see the README.)

---

### Priority order if doing it gradually
Telegram → Claude → CMC → TWAK + wallet + bankroll → tokens → `.env`.
The minimum for a first real-execution test: **TWAK + a funded wallet + 1 token**.

Then run `python run_agent.py --paper` (simulated, zero risk) before going live, or deploy
24/7 with [`DEPLOY.md`](../DEPLOY.md).
