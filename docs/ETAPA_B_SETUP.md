# 🔑 Etapa B — Desbloqueio (guia passo a passo)

O código está pronto. Esta etapa é o que **só você** pode fazer: obter credenciais,
criar/fundear a carteira e popular os tokens. Faça na ordem abaixo (da mais fácil
para a mais técnica). Marque conforme conclui.

> ⚠️ Abra um **terminal NOVO** antes de começar, para `python`, `node` e `twak`
> já estarem no PATH. Teste: `python --version`, `node --version`, `twak --version`.
> Nunca cole chave privada/seed em chat. Não commite o `.env`.

---

## 1. Bot do Telegram (5 min, grátis) ✅ mais fácil

1. No Telegram, abra **@BotFather** → `/newbot` → dê um nome e um @username.
2. Ele devolve um **token** tipo `8123456789:AAH...`. Guarde → vai em `TELEGRAM_BOT_TOKEN`.
3. Descubra seu **user ID**: fale com **@userinfobot** (ele responde seu `Id`).
   Esse número vai em `TELEGRAM_MASTER_USER_ID` (é a trava que só deixa VOCÊ comandar).

## 2. Claude API (Anthropic) — o "cérebro" (5 min)

1. Acesse **console.anthropic.com** → crie conta / login.
2. **API Keys** → *Create Key* → copie (começa com `sk-ant-...`).
3. Vai em `ANTHROPIC_API_KEY`. (Vencedores ganham créditos de Claude; pra dev, o custo é baixíssimo.)

## 3. CoinMarketCap — os "olhos" (10 min)

1. Acesse **coinmarketcap.com/api** → crie uma conta de developer → pegue a **API Key**.
2. Veja o **AI Agent Hub**: **coinmarketcap.com/api/agent** (MCP + x402).
3. Coloque a key em `CMC_API_KEY`.
   - *Opcional (prêmio x402):* o pagamento por chamada usa **USDC na rede Base**.
     Dá pra deixar pra depois — em dev usamos a API key. Quando formos buscar o
     prêmio de x402, eu configuro o pagamento via `twak x402`.

## 4. Trust Wallet Agent Kit (TWAK) — as "mãos/cofre" (15 min)

A forma mais simples é o onboarding interativo (rode **no seu terminal**, é interativo):

```bash
twak setup
```

Ele guia: (a) credenciais, (b) wiring, (c) criar carteira. Se preferir manual:

1. Acesse **portal.trustwallet.com** → crie um app → gere **API key**.
2. Copie **Access ID** e **HMAC Secret** (o secret aparece UMA vez).
3. No terminal:
   ```bash
   twak init --api-key <ACCESS_ID> --api-secret <HMAC_SECRET>
   ```
   (salva em `~/.twak/credentials.json`)
4. Coloque também em `.env`: `TWAK_ACCESS_ID` e `TWAK_HMAC_SECRET`.

## 5. Criar e fundear a carteira do agente (15 min) 💰

1. Crie a carteira do agente (autocustódia — a seed fica num keystore **cifrado** do twak,
   protegido pela senha):
   ```bash
   twak wallet create --password "UMA_SENHA_FORTE"
   ```
   → Guarde a senha em `WALLET_PASSWORD`. **Anote a seed** em local seguro (offline).
   No deploy hospedado, esse keystore cifrado vai pro provedor como variável de ambiente
   (ver `DEPLOY.md`); a senha também. A chave nunca aparece no site/navegador/código.
2. Veja o endereço do agente na BSC:
   ```bash
   twak wallet address --chain bsc
   ```
3. Do seu app **Trust Wallet pessoal** (celular), envie pela rede **BNB Smart Chain (BEP-20)**:
   - Uma **banca pequena de teste** em **USDT** (ex.: $20–50).
   - Um pouco de **BNB** para o gás (ex.: $2–3).
4. Confira o saldo:
   ```bash
   twak wallet portfolio --chains bsc --password "UMA_SENHA_FORTE"
   ```
5. Em `OWNER_WALLET_ADDRESS` coloque o endereço da sua **carteira pessoal**
   (destino do saque / boomerang).

> 💡 Você mantém o controle total: importando a seed da carteira do agente no app
> da Trust Wallet, pode sacar manualmente a qualquer momento.

## 6. Popular os 149 tokens elegíveis (`data/eligible_tokens.json`)

O agente só negocia tokens elegíveis. Para **começar**, basta o **subconjunto líquido**
(uns 12), que é o que a estratégia conservadora usa. Os 149 completos são opcionais.

**Como obter cada endereço com segurança:** na página do token no CoinMarketCap,
seção **Contracts**, copie o endereço **BNB Smart Chain (BEP-20)** e confira no
**bscscan.com**. Formato no arquivo:

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

> 🤝 Eu posso **pré-preencher** esse subconjunto com os endereços canônicos
> (Binance-Peg) para você só **conferir** contra a CMC/BscScan — me peça.

## 7. Montar o `.env`

```bash
copy .env.example .env   # Windows
```
Preencha: `WALLET_PASSWORD`, `OWNER_WALLET_ADDRESS`, `TWAK_ACCESS_ID`,
`TWAK_HMAC_SECRET`, `CMC_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_MASTER_USER_ID`. (No Windows, defina também `TWAK_BIN` e `NODE_DIR` se
o `twak` não estiver no PATH — ver README.)

## 8. Me avisar ✅

Quando terminar (ou só parte), me diga. Eu rodo a **Etapa C**: validação na mainnet
com a banca mínima, confirmo os 2 detalhes finais (formato de token no `swap` e
campos JSON de sucesso) e começamos o **soak/tuning**.

---

### Ordem de prioridade se quiser fazer aos poucos
Telegram → Claude → CMC → TWAK + carteira + banca → tokens → `.env`.
O mínimo para um primeiro teste de execução real: **TWAK + carteira fundeada + 1 token**.
