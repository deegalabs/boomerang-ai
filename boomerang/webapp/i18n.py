"""i18n simples (server-side) para a web do Boomerang AI.

Idioma vem de ?lang= ou cookie 'lang' (default 'en'). Mantém o conteúdo
sincronizado nos dois idiomas sem build nem JS pesado.
"""
from __future__ import annotations

LANGS = ("en", "pt")
DEFAULT = "en"

# Itens de navegação (rota → rótulo por idioma)
NAV = [
    ("/",       {"en": "Home",   "pt": "Início"}),
    ("/docs",   {"en": "Docs",   "pt": "Docs"}),
    ("/guides", {"en": "Guides", "pt": "Guias"}),
    ("/live",   {"en": "Live",   "pt": "Ao vivo"}),
]

TR: dict[str, dict[str, str]] = {
    "en": {
        "tagline": "Autonomous trading agent on BNB Chain",
        "connect": "Connect Wallet",
        "console": "Console",
        "foot_rights": "Built for BNB Hack · Track 1 · CoinMarketCap × Trust Wallet × BNB Chain",
        "foot_note": "Self-custodial. On-chain verifiable. Not financial advice.",
        # Foundation showcase
        "fnd_eyebrow": "Design System — Phase 0",
        "fnd_title": "The visual language",
        "fnd_sub": "The foundation every page is built on — identity, type, color, and components, crafted for clarity and trust.",
        "fnd_identity": "Identity",
        "fnd_identity_d": "A kinetic boomerang — capital thrown out returns with profit. Sharp BNB gold over deep space.",
        "fnd_type": "Typography",
        "fnd_color": "Color",
        "fnd_components": "Components",
        "fnd_buttons": "Buttons",
        "fnd_pills": "Status pills",
        "fnd_stats": "Live data tiles",
        "fnd_table": "Tables",
        "fnd_controls": "Controls",
        "s_equity": "Equity", "s_pnl": "Today's PnL", "s_drawdown": "Drawdown", "s_position": "Open position",
        "th_token": "Token", "th_entry": "Entry", "th_now": "Now", "th_pnl": "PnL",
        "lbl_focus": "Focus token", "lbl_stop": "Stop-loss", "lbl_target": "Take-profit",
        # ---- Landing ----
        "l_hero_tag": "BNB Hack · Track 1 · Autonomous Trading Agent",
        "l_hero_a": "Market attention, captured and set in",
        "l_hero_b": "motion.",
        "l_hero_sub": "Boomerang AI is an autonomous agent on BNB Chain. It reads retail attention spikes on CoinMarketCap, decides with AI, and executes swaps with self-custody. Around the clock, without emotion, under rules you set.",
        "l_cta_live": "See it live", "l_cta_docs": "Documentation", "l_cta_github": "GitHub",
        "l_hero_trust": "Self-custodial · On-chain verifiable · CoinMarketCap × Trust Wallet × BNB Chain",
        "l_prob_eye": "The gap",
        "l_prob_title": "Attention arrives before liquidity.",
        "l_prob_body": "When a coin goes viral, retail rushes to CoinMarketCap first. Searches, trending and sentiment spike. But liquidity only reaches the chain minutes later. That gap is a window. No human watches 149 tokens, 24 hours a day, and acts in seconds. An agent does.",
        "l_sol_eye": "The solution",
        "l_sol_title": "That is where Boomerang AI comes in.",
        "l_sol_body": "An agent that turns the window into an edge. It watches the signal, validates the risk on-chain, and enters and exits with discipline. The capital always returns to its owner, like a boomerang.",
        "l_how_eye": "How it works",
        "l_how_title": "Three shields. One decision.",
        "l_s1_name": "Analytical", "l_s1_tag": "CoinMarketCap + Claude",
        "l_s1_body": "Reads attention and momentum. The AI decides with a confidence threshold, never on impulse.",
        "l_s2_name": "On-chain", "l_s2_tag": "BNB Chain",
        "l_s2_body": "Validates real liquidity (V2 and V3), simulates the round trip, and rejects hidden taxes and out-of-sync prices. If it cannot exit cleanly, it does not enter.",
        "l_s3_name": "Execution", "l_s3_tag": "Trust Wallet Agent Kit",
        "l_s3_body": "Signs and sends the transaction locally. The key never leaves the machine. Real self-custody.",
        "l_risk_eye": "Risk management",
        "l_risk_title": "Built to protect capital.",
        "l_risk_body": "Per-trade stop-loss, a trailing stop that protects gains, a take-profit target, and a global circuit breaker that liquidates everything before the drawdown limit. Small positions, contained losses, survival first.",
        "l_risk_1": "Stop-loss", "l_risk_2": "Trailing stop", "l_risk_3": "Take-profit", "l_risk_4": "Circuit breaker",
        "l_sec_eye": "Security & privacy",
        "l_sec_title": "Your money, your rules, your key.",
        "l_sec1_t": "Self-custody", "l_sec1_d": "The private key stays local. It never reaches the browser.",
        "l_sec2_t": "Process isolation", "l_sec2_d": "The brain holds no key. The vault never listens to the internet.",
        "l_sec3_t": "Injection-proof", "l_sec3_d": "The AI only receives structured metrics, never raw manipulable text.",
        "l_sec4_t": "Owner lock", "l_sec4_d": "Only you command it, verified by your wallet signature.",
        "l_proof_eye": "Live proof",
        "l_proof_title": "Don't trust. Verify.",
        "l_proof_body": "Every operation happens on-chain, at the agent's public address. Watch equity, PnL and each trade live, with a direct link to BscScan.",
        "l_proof_cta": "Open live panel", "l_proof_addr": "Agent wallet", "l_proof_tx": "Sample trade",
        "l_spon_eye": "Powered by",
        "l_spon_title": "The best of three worlds.",
        "l_spon_cmc_r": "The eyes", "l_spon_cmc_d": "Attention signals.",
        "l_spon_tw_r": "The hands and the vault", "l_spon_tw_d": "Execution and custody.",
        "l_spon_bnb_r": "The field", "l_spon_bnb_d": "Liquidity, speed, low cost.",
        "l_cta_title": "Ready to watch the boomerang fly?",
    },
    "pt": {
        "tagline": "Agente de trading autônomo na BNB Chain",
        "connect": "Conectar Carteira",
        "console": "Console",
        "foot_rights": "Feito para o BNB Hack · Track 1 · CoinMarketCap × Trust Wallet × BNB Chain",
        "foot_note": "Autocustódia. Verificável on-chain. Não é recomendação financeira.",
        "fnd_eyebrow": "Design System — Fase 0",
        "fnd_title": "A linguagem visual",
        "fnd_sub": "A fundação sobre a qual cada página é construída — identidade, tipografia, cor e componentes, feitos para clareza e confiança.",
        "fnd_identity": "Identidade",
        "fnd_identity_d": "Um boomerang cinético — o capital lançado volta com lucro. Dourado da BNB afiado sobre o espaço profundo.",
        "fnd_type": "Tipografia",
        "fnd_color": "Cor",
        "fnd_components": "Componentes",
        "fnd_buttons": "Botões",
        "fnd_pills": "Selos de status",
        "fnd_stats": "Blocos de dados ao vivo",
        "fnd_table": "Tabelas",
        "fnd_controls": "Controles",
        "s_equity": "Patrimônio", "s_pnl": "PnL do dia", "s_drawdown": "Drawdown", "s_position": "Posição aberta",
        "th_token": "Moeda", "th_entry": "Entrada", "th_now": "Agora", "th_pnl": "PnL",
        "lbl_focus": "Moeda-foco", "lbl_stop": "Stop-loss", "lbl_target": "Lucro-alvo",
        # ---- Landing ----
        "l_hero_tag": "BNB Hack · Track 1 · Agente de Trading Autônomo",
        "l_hero_a": "A atenção do mercado, capturada e posta em",
        "l_hero_b": "movimento.",
        "l_hero_sub": "O Boomerang AI é um agente autônomo na BNB Chain. Ele lê os picos de atenção do varejo na CoinMarketCap, decide com IA e executa swaps com autocustódia. 24 horas por dia, sem emoção, sob regras que você define.",
        "l_cta_live": "Ver ao vivo", "l_cta_docs": "Documentação", "l_cta_github": "GitHub",
        "l_hero_trust": "Autocustódia · Verificável on-chain · CoinMarketCap × Trust Wallet × BNB Chain",
        "l_prob_eye": "O problema",
        "l_prob_title": "A atenção chega antes da liquidez.",
        "l_prob_body": "Quando uma moeda viraliza, o varejo corre primeiro para a CoinMarketCap. Buscas, trending e sentimento disparam. Mas a liquidez só chega on-chain minutos depois. Essa diferença é uma janela. Nenhum humano vigia 149 tokens, 24 horas por dia, e age em segundos. Um agente, sim.",
        "l_sol_eye": "A solução",
        "l_sol_title": "É aí que o Boomerang AI entra.",
        "l_sol_body": "Um agente que transforma essa janela em vantagem. Ele observa o sinal, valida o risco on-chain, e entra e sai com disciplina. O capital sempre volta ao dono, como um boomerang.",
        "l_how_eye": "Como funciona",
        "l_how_title": "Três escudos. Uma decisão.",
        "l_s1_name": "Analítico", "l_s1_tag": "CoinMarketCap + Claude",
        "l_s1_body": "Lê atenção e momentum. A IA decide com um corte de confiança, nunca no impulso.",
        "l_s2_name": "On-chain", "l_s2_tag": "BNB Chain",
        "l_s2_body": "Valida liquidez real (V2 e V3), simula a ida e a volta, e rejeita taxa oculta e preço fora de sincronia. Se não dá pra sair limpo, não entra.",
        "l_s3_name": "Execução", "l_s3_tag": "Trust Wallet Agent Kit",
        "l_s3_body": "Assina e envia a transação localmente. A chave nunca sai da máquina. Autocustódia de verdade.",
        "l_risk_eye": "Gestão de risco",
        "l_risk_title": "Feito para proteger o capital.",
        "l_risk_body": "Stop-loss por trade, trailing que protege o lucro, lucro-alvo, e um circuit breaker global que liquida tudo antes do limite de drawdown. Posições pequenas, perdas contidas, sobrevivência em primeiro lugar.",
        "l_risk_1": "Stop-loss", "l_risk_2": "Trailing", "l_risk_3": "Lucro-alvo", "l_risk_4": "Circuit breaker",
        "l_sec_eye": "Segurança e privacidade",
        "l_sec_title": "Seu dinheiro, suas regras, sua chave.",
        "l_sec1_t": "Autocustódia", "l_sec1_d": "A chave privada fica local. Nunca vai pro navegador.",
        "l_sec2_t": "Isolamento", "l_sec2_d": "O cérebro não tem chave. O cofre não ouve a internet.",
        "l_sec3_t": "Anti-injeção", "l_sec3_d": "A IA só recebe métricas estruturadas, nunca texto bruto manipulável.",
        "l_sec4_t": "Trava de dono", "l_sec4_d": "Só você comanda, verificado pela assinatura da sua carteira.",
        "l_proof_eye": "Prova ao vivo",
        "l_proof_title": "Não confie. Verifique.",
        "l_proof_body": "Toda operação acontece on-chain, no endereço público do agente. Veja patrimônio, PnL e cada trade ao vivo, com link direto pra BscScan.",
        "l_proof_cta": "Abrir painel ao vivo", "l_proof_addr": "Carteira do agente", "l_proof_tx": "Trade de exemplo",
        "l_spon_eye": "Construído com",
        "l_spon_title": "O melhor de três mundos.",
        "l_spon_cmc_r": "Os olhos", "l_spon_cmc_d": "Sinais de atenção.",
        "l_spon_tw_r": "As mãos e o cofre", "l_spon_tw_d": "Execução e custódia.",
        "l_spon_bnb_r": "O campo", "l_spon_bnb_d": "Liquidez, velocidade, baixo custo.",
        "l_cta_title": "Pronto para ver o boomerang voar?",
    },
}


def pick_lang(raw: str | None, cookie: str | None) -> str:
    for cand in (raw, cookie):
        if cand in LANGS:
            return cand  # type: ignore[return-value]
    return DEFAULT


def strings(lang: str) -> dict[str, str]:
    return TR.get(lang, TR[DEFAULT])


def nav_items(lang: str) -> list[tuple[str, str]]:
    return [(path, label[lang]) for path, label in NAV]
