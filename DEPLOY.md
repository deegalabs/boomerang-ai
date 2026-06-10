# Deploy — Boomerang AI (Fase 5)

> **Caminho recomendado: Railway** (abaixo). A seção VPS/systemd vem depois, como alternativa.

## Railway (deploy oficial)

Arquitetura real: **um único serviço** na Railway roda o **site público + o agente 24/7** no mesmo container ([`Dockerfile`](Dockerfile) Python 3.12 + Node 20 + TWAK CLI). O entrypoint [`railway_start.py`](railway_start.py) sobe o **site** (uvicorn, thread principal, responde `/healthz` na hora) e o **agente** (thread própria), compartilhando um **volume** (`/app/state`) para o estado, então o `/live` mostra os trades reais. É a **mesma carteira** de trade: o keystore **cifrado** (`~/.twak/wallet.json`) e o estado inicial são materializados no boot a partir de env vars base64 — nada de migrar fundos, nada de chave crua.

> Por que não Vercel: o site é Python renderizado no servidor (Starlette + Jinja2) e o agente é um processo 24/7. Vercel é serverless/estático e não roda nenhum dos dois. Por isso, tudo na Railway.

```bash
# 1. CLI + login
npm i -g @railway/cli
railway login

# 2. projeto + volume de estado (na raiz do repo)
railway init                          # cria o projeto
railway volume add -m /app/state      # posições sobrevivem a restart

# 3. variáveis: migra os segredos do .env + o keystore cifrado + o estado,
#    SEM imprimir valores (gera TWAK_WALLET_JSON_B64 e STATE_SEED_B64 em base64)
python scripts/railway_setvars.py
railway variables --set "SESSION_SECRET=$(python -c 'import secrets;print(secrets.token_hex(32))')"
railway variables --set "OWNER_WALLET_ADDRESS=0x...sua_carteira_pessoal..."

# 4. build/deploy + URL pública
railway up --detach
railway domain
```

O [`railway.json`](railway.json) usa o builder Dockerfile, start `python railway_start.py` e healthcheck `/healthz`. O [`.dockerignore`](.dockerignore) garante que `.env`, `identity_wallet/` e `state/` **nunca** entram na imagem — os segredos chegam só como variáveis de ambiente protegidas da Railway, em runtime.

**Verificar** (`railway logs -d`): deve aparecer `Equity inicial (on-chain)`, `Estado restaurado`, `Identidade ERC-8004`, `Bot do Telegram em polling` e o `CICLO`. O `/api/live` público mostra `IN_POSITION`/equity/holdings reais.

> **Segurança (cloud):** o keystore cifrado **e** a `WALLET_PASSWORD` vivem na Railway, então a chave é decifrável no provedor. É inerente a qualquer bot hospedado; a banca pequena limita o risco. Os segredos ficam só nas env vars (nunca no repo/imagem). **Rotacione o `TELEGRAM_BOT_TOKEN`** (no @BotFather) se ele já tiver aparecido em log antes do filtro de logging.

### x402 real

O `twak` agora roda **dentro do container** (mesma imagem). A rota `/x402` do próprio site injeta o header que o MCP da CMC exige, então o pagamento pay-per-call pode ser liquidado da nuvem ou da máquina local apontando para a URL pública:

```bash
twak x402 request --method POST \
  --body '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_global_metrics_latest","arguments":{}}}' \
  --max-payment 10000 --prefer-network base --prefer-method eip3009 --yes \
  https://SEU-APP.up.railway.app/x402
```

Resposta 200 com dados = pagamento liquidado on-chain ($0.01 em USDC na Base). Em operação normal os dados vêm via **REST grátis** (não defina `X402_ENDPOINT`); o x402 real é prova/demo, não custo por ciclo.

> **Não rode o agente local junto** com o da Railway: dois agentes na mesma carteira entram em conflito.

---

## VPS / systemd (alternativa)

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
