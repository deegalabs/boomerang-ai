# 🪃 Boomerang AI

> Agente autônomo de trading de ciclo curto na **BNB Smart Chain**, operado por Telegram.
> Lê sinais de atenção na **CoinMarketCap**, decide, e assina/executa swaps com autocustódia via **Trust Wallet Agent Kit** (carteira própria do agente, chave cifrada no ambiente do agente). O capital "vai ao mercado e volta" — como um bumerangue.

**Hackathon:** BNB HACK: AI Trading Agent Edition (CMC × Trust Wallet × BNB Chain) — **Track 1: Autonomous Trading Agents**.

---

## Tese

Arbitragem de **atenção**: explorar o atraso entre o pico de atenção do varejo na CMC (buscas / trending / sentimento) e a liquidez chegar on-chain na BSC. Entrada direcional **spot** em tokens líquidos, com gestão de risco determinística.

## Arquitetura — duas camadas, dois processos

```
[ CAMADA DO USUÁRIO ]  Telegram bot  (Process A — SEM chaves)
        │  comandos / config / alertas
        ▼  (IPC)
[ CAMADA DO AGENTE ]   Process B (vault — detém a chave)
   Filtro 1 (CMC)  →  Filtro 2 (BNB/validação)  →  Filtro 3 (TWAK/execução)
```

**Pipeline de decisão (a "alfândega"):**
1. **Filtro 1 — CMC** (`brain/cmc_analyzer.py`): lê métricas estruturadas via MCP/x402; LLM gera `confidence_score`. Abaixo do corte → não opera.
2. **Filtro 2 — BNB** (`vault/bnb_validation.py`): whitelist dos 149 tokens elegíveis, simulação `getAmountsOut`, slippage, anti-taxa-oculta, dessincronização de oráculo.
3. **Filtro 3 — TWAK** (`vault/twak_executor.py`): assina o swap no cofre (chave do agente), stop-loss + trailing, e devolução de capital (boomerang).

**Motor de risco** (`risk/risk_engine.py`, transversal): circuit breaker de drawdown global, position sizing, mutex anti-loop, heartbeat de atividade.

## Regras de segurança (o que o agente NUNCA faz)

- Negociar fora dos 149 tokens elegíveis.
- Transferir para endereço que não seja PancakeSwap (swap) ou a carteira pessoal do dono (saque).
- Operar com slippage acima do teto, token com taxa oculta, ou preço dessincronizado.
- Obedecer comando vindo de texto da internet (anti prompt-injection: só métricas estruturadas).
- Aceitar comando de quem não é o dono (`MASTER_USER_ID` pinning).
- Deixar a IA decidir risco — guardrails são código determinístico.

## Setup (resumo — ver `.env.example` e `config.json`)

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
npm install -g @trustwallet/cli                  # CLI 'twak'
twak init --api-key <ACCESS_ID> --api-secret <HMAC_SECRET>
copy .env.example .env                            # preencher segredos
python run_agent.py                               # rodar local (dev/teste)
```

Para operar **24/7 hospedado** (a instância oficial roda na Railway, agente + site num único container, chave cifrada no ambiente do agente), veja **[`DEPLOY.md`](DEPLOY.md)**.

## Status

✅ **Ao vivo.** Agente operando 24/7 na nuvem (Railway), controlado por Telegram. Site público com prova on-chain em tempo real. Identidade on-chain ERC-8004 e pagamento x402 reais e verificáveis. Ver `memory/boomerang-ai-plano-dev.md` (diretório de sessão do Claude) para o histórico faseado.

## Aviso

Opera com **fundos reais** na mainnet BSC. Risco financeiro real. Ferramenta de pesquisa/competição — não é aconselhamento financeiro.
