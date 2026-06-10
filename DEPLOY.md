# Deploy — Boomerang AI (Fase 5)

Como colocar no ar, numa VPS Linux, os três processos do Boomerang AI:

1. **Agente** (`run_agent.py`) — opera 24/7, controlado pelo Telegram.
2. **Site público** (`boomerang.webapp.site:app`) — landing, docs, guia, prova ao vivo e Console demo.
3. **Proxy x402** (`boomerang.webapp.x402_proxy`) — dá ao `twak x402` o endpoint público que ele exige (destrava o pagamento real por chamada à CoinMarketCap).

A identidade on-chain ERC-8004 (agentId **131071**) já está registrada na BNB mainnet; nada a fazer aqui além de versionar `boomerang/identity/agent_card.json` (já está).

---

## 1. Pré-requisitos

- VPS Linux (Ubuntu 22.04+), um domínio apontando pro IP (ex.: `boomerang.deegalabs.ai`).
- Python 3.12, Node 20+ (para o `twak`), `git`, e um reverse proxy com TLS (recomendo **Caddy** pela simplicidade).
- Portas 80/443 abertas.

## 2. Código, dependências e segredos

```bash
git clone https://github.com/deegalabs/boomerang-ai.git
cd boomerang-ai
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# TWAK CLI (autocustódia + x402). Instala local, como na máquina de dev:
corepack pnpm add @trustwallet/cli   # gera ./node_modules/.bin/twak

cp .env.example .env   # depois preencha (NUNCA versione .env)
```

No `.env` (valores reais, fora do git):

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
# Senha do keystore da identidade ERC-8004 (cai na WALLET_PASSWORD se ausente):
# BNB_IDENTITY_PASSWORD=...
# Endpoint x402 público (ver passo 5) — destrava o pagamento real via twak:
# X402_ENDPOINT=https://boomerang.deegalabs.ai/x402
DASHBOARD_TOKEN=...        # opcional, dashboard só-leitura do agente (porta 8080)
```

> A carteira de identidade (`identity_wallet/`) e o `.env` são git-ignorados. Se for migrar a identidade da máquina de dev, copie a pasta `identity_wallet/` por canal seguro (ela contém a chave). Caso contrário, rode `python scripts/register_identity.py` na VPS para gerar uma nova.

## 3. Site público (systemd)

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

## 4. Agente (systemd)

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

## 5. x402 real (proxy + Caddy)

O `twak x402` recusa endereços privados/loopback — precisa de um endpoint **público com TLS**. O proxy roda em loopback e o Caddy expõe `/x402` publicamente, injetando nada (o proxy já injeta o header `Accept` do MCP).

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

Depois aponte o agente pro endpoint público no `.env`:

```ini
X402_ENDPOINT=https://boomerang.deegalabs.ai/x402
```

Com isso, o `twak x402 request` paga as chamadas à CMC ($0.01 cada) com o **USDC que já está na carteira de trade** — sem fundo novo, sem ETH, sem expor chave.

## 6. Caddy (TLS + rotas)

`/etc/caddy/Caddyfile`:

```
boomerang.deegalabs.ai {
    # x402: caminho público que o twak chama -> proxy local -> CMC
    handle_path /x402* {
        reverse_proxy 127.0.0.1:8402
    }
    # site público
    reverse_proxy 127.0.0.1:8090
}
```

O Caddy provisiona o certificado Let's Encrypt automaticamente.

## 7. Subir e verificar

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now boomerang-x402 boomerang-site boomerang-agent caddy

# saúde do site + identidade
curl -s https://boomerang.deegalabs.ai/healthz

# x402 real (uma chamada paga $0.01 a partir da carteira de trade):
. .venv/bin/activate
twak x402 request --method POST \
  --body '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_crypto_quotes_latest","arguments":{"symbol":"BNB"}}}' \
  --max-payment 10000 --prefer-network base --prefer-method eip3009 --yes \
  https://boomerang.deegalabs.ai/x402
```

Uma resposta 200 com dados = pagamento x402 liquidado on-chain. A partir daí o agente pode usar dados pagos da CMC definindo `X402_ENDPOINT` (senão segue no REST grátis, que é o padrão de custo zero).

## Alternativa: x402 pela carteira de identidade (sem twak)

O cliente `boomerang/payments/x402_cmc.py` assina o pagamento direto com o BNB AI Agent SDK (não precisa de proxy nem de endereço público). Basta a **carteira de identidade** (`0xd06be7…`) ter USDC na Base. Para uma chamada de prova:

```bash
python scripts/x402_pay.py            # paga $0.01 e imprime os dados
```

Útil se preferir fundear a identidade (ex.: saque de corretora, que cobre o gás) em vez de usar o twak na VPS.
