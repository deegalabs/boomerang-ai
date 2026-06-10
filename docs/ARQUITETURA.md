# 🪃 Arquitetura do Boomerang AI

Agente autônomo de trading de ciclo curto na BNB Chain, operado por Telegram.
Documento mapeado nos arquivos reais do projeto.

---

## 1. Visão macro — duas camadas, dois processos

```
┌──────────────────────────────────────────────────────────────────────────┐
│  CAMADA DO USUÁRIO  (Process A — NUNCA toca em chaves privadas)            │
│                                                                            │
│   📱 Telegram Bot   boomerang/interface/telegram_bot.py                    │
│      • botões (InlineKeyboards)   • MASTER_USER_ID pinning                  │
│      • /start /status /panic /pausar /sacar   • alertas em tempo real      │
└───────────────┬───────────────────────────────▲───────────────────────────┘
                │ intents de controle           │ alertas (AlertBus)
                │ (configure/start/panic/        │ boomerang/ipc/events.py
                ▼  withdraw)                      │
┌──────────────────────────────────────────────────────────────────────────┐
│  CAMADA DO AGENTE  (Process B — detém acesso à assinatura via TWAK)        │
│                                                                            │
│   🧠 Orquestrador   boomerang/agent.py                                      │
│      scan_loop (intervalo) + monitor_loop (2s)                              │
│                                                                            │
│   ┌─────────────┐   ┌──────────────┐   ┌───────────────┐                  │
│   │ FILTRO 1    │ → │ FILTRO 2     │ → │ FILTRO 3      │                  │
│   │ CMC/Claude  │   │ BNB validação│   │ TWAK execução │                  │
│   └─────────────┘   └──────────────┘   └───────────────┘                  │
│         ▲                  ▲                    ▲                          │
│         │           🛡️ MOTOR DE RISCO (transversal)                       │
│         │           boomerang/risk/risk_engine.py                          │
│         │   circuit breaker · sizing · trailing · heartbeat · mutex        │
└─────────┼──────────────────┼────────────────────┼─────────────────────────┘
          │                  │                    │
     CoinMarketCap       BNB Chain RPC         Trust Wallet
     (MCP + x402)        (PancakeSwap)         Agent Kit (twak)
```

**Princípio de isolamento:** o bot/site (que fala com a internet) **nunca** acessa
a chave. Ele só envia *intenções de controle* e recebe *alertas*. A chave vive no
**keystore cifrado** do `twak`, no lado do agente. (v1: barramento em processo, um
único container no deploy; a costura permite virar IPC real entre processos na fase
de endurecimento.)

> **Onde roda (deploy):** a instância oficial roda na **Railway**, agente + site num
> único container. O keystore cifrado e a senha vivem como variáveis de ambiente
> protegidas do provedor (não no repositório nem na imagem). A assinatura acontece no
> ambiente do agente, nunca no navegador/site. Continua **autocustódia** (carteira
> própria do agente, saque travado no dono), mas a chave não fica "na sua máquina".

---

## 2. O pipeline de um trade (a "alfândega")

Cada ciclo de scan (`agent.run_cycle`) atravessa três filtros em série. Basta um
reprovar para o trade ser abortado **antes** de tocar no dinheiro.

```
                    ┌──────────────────────────────────────────────┐
  a cada ciclo  →   │ 🛡️ MOTOR DE RISCO (pré-checagem)              │
                    │  • equity (twak portfolio) → atualiza pico    │
                    │  • drawdown ≥ 23%? → PÂNICO (liquida+trava)   │
                    │  • heartbeat? (>20h sem trade)                │
                    │  • pode abrir? (cooldown, nº posições, banca) │
                    └───────────────────┬──────────────────────────┘
                                        │ ok
   ┌────────────────────────────────────▼─────────────────────────────────┐
   │ 1️⃣ FILTRO 1 — Cérebro (cmc_analyzer.py)                                │
   │   busca métricas estruturadas na CMC (MCP) → SANITIZA (anti-injeção)   │
   │   → Claude (tool forçada) → {confidence_score, action}                 │
   │   corte determinístico: score < mínimo → HOLD                          │
   └────────────────────────────────────┬──────────────────────────────────┘
                                         │ BUY (score ≥ 90 no modo conservador)
   ┌─────────────────────────────────────▼─────────────────────────────────┐
   │ 2️⃣ FILTRO 2 — Validação on-chain (bnb_validation.py)                   │
   │   whitelist dos 149 · getAmountsOut (slippage) · round-trip (taxa      │
   │   oculta) · divergência CMC×pool (oráculo) — tudo leitura, custo zero  │
   └─────────────────────────────────────┬─────────────────────────────────┘
                                          │ aprovado (min_out calculado)
   ┌──────────────────────────────────────▼────────────────────────────────┐
   │ 3️⃣ FILTRO 3 — Execução (twak_executor.py)                              │
   │   sob mutex: twak swap USDT→token (assinatura no cofre) → abre posição │
   │   com stop-loss inicial. Emite alerta TRADE_OPENED.                    │
   └─────────────────────────────────────────────────────────────────────┘
```

---

## 3. O monitor de saída (stop / trailing)

Loop paralelo de 2s (`agent.check_positions`), aproveitando o bloco rápido da BSC:

```
para cada posição:
   preço = bnb_validation.onchain_price_usd(token)     # leitura, sem gás
   sinal = risk.evaluate_position(posição, preço)
     ├─ HOLD                → mantém
     ├─ SELL_STOP_LOSS      → caiu além do stop → vende
     └─ SELL_TRAILING       → subiu +5% (trava break-even, acompanha o pico),
                              depois recuou → vende NO LUCRO
   se vender → twak swap token→USDT → alerta TRADE_CLOSED (com PnL)
```

---

## 4. O fluxo do dinheiro (o "bumerangue")

```
[ Carteira Pessoal ] ──deposita banca──► [ Carteira do Agente ] ──opera──► PancakeSwap
       ▲                                        │
       └──────── /sacar  ou  PÂNICO  ◄──────────┘  (twak transfer --confirm-to)
                 (devolve tudo p/ stable e manda de volta)
```

- **Modo competição (22–28/jun):** opera contínuo, compondo a banca.
- **Boomerang (devolução automática ao fim do ciclo):** melhoria futura/demo.
- **`--confirm-to`** trava o destino do saque = blindagem anti-drenagem.

---

## 5. As duas camadas de regras

| Camada DEV (imutável no código)            | Camada USUÁRIO (via Telegram)        |
|--------------------------------------------|--------------------------------------|
| whitelist dos 149 tokens                   | foco de tokens (subconjunto líquido) |
| circuit breaker de drawdown (23%/DQ 30%)   | stop-loss (2% / 4% / 5%)             |
| teto de slippage (0.5%)                    | modo (conservador ≥90 / agressivo ≥80)|
| bloqueio de destino (anti-drenagem)        | (banca por trade = % fixo do equity) |
| mínimo de trades / heartbeat               |                                      |

`config.json` = `dev_safety` + `hackathon` (travados) e `user` (ajustável).

---

## 6. Blindagem de segurança (threat model → defesa)

| Ataque                          | Defesa (arquivo)                                   |
|---------------------------------|----------------------------------------------------|
| Injeção de prompt (notícia/social) | sanitize_metrics — só números/rótulos (cmc_analyzer)|
| Sequestro do bot                | MASTER_USER_ID pinning (telegram_bot)              |
| Sandwich / MEV                  | slippage + amountOutMin (bnb_validation)           |
| Taxa oculta / honeypot          | round-trip retention (bnb_validation)              |
| Oráculo atrasado ("faca caindo")| divergência CMC×pool (bnb_validation)              |
| Loop infinito / spam de gás     | mutex + cooldown (risk_engine)                     |
| Roubo de chave                  | chave no keystore cifrado do twak; bot/site sem acesso |
| Exposição no host (cloud)       | segredos como env vars protegidas; nunca no repo/imagem; banca pequena limita o risco |
| Drawdown catastrófico / DQ      | circuit breaker determinístico (risk_engine)       |

---

## 7. Mapeamento para patrocinadores e prêmios

- **CoinMarketCap (Agent Hub):** Filtro 1 consome dados via MCP + paga via x402
  → concorre ao "Best Use of Agent Hub".
- **Trust Wallet (TWAK):** única camada de execução, múltiplas superfícies
  (assinatura + modo autônomo + x402), autocustódia → mira a rubrica do
  "Best Use of TWAK" (ver memory/boomerang-ai-twak-rubrica).
- **BNB AI Agent SDK:** identidade on-chain do agente (ERC-8004) → "Best Use of BNB SDK".
- **Track 1 (PnL):** os guardrails determinísticos maximizam retorno sem estourar
  o drawdown que desclassifica.
```
```
