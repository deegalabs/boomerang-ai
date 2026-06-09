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
