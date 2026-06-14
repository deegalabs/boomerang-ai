"""Carregamento de configuração (config.json) e segredos (.env).

Separa claramente as três camadas de regras:
  - user       → ajustável pelo dono via Telegram
  - dev_safety → leis de segurança imutáveis (código)
  - hackathon  → regras fixas do evento

Segredos NUNCA vêm do config.json; só do ambiente (.env / variáveis de SO).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv é opcional em produção (segredos podem vir do SO)
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Secrets:
    """Segredos vindos do ambiente. Nunca logar nem serializar."""

    agent_private_key: str | None
    wallet_password: str | None
    owner_wallet_address: str | None
    twak_access_id: str | None
    twak_hmac_secret: str | None
    cmc_api_key: str | None
    anthropic_api_key: str | None
    llm_model: str
    telegram_bot_token: str | None
    telegram_master_user_id: int | None
    bsc_rpc_url_override: str | None

    @classmethod
    def from_env(cls) -> "Secrets":
        master_id = os.getenv("TELEGRAM_MASTER_USER_ID")
        return cls(
            agent_private_key=os.getenv("AGENT_PRIVATE_KEY"),
            wallet_password=os.getenv("WALLET_PASSWORD"),
            owner_wallet_address=os.getenv("OWNER_WALLET_ADDRESS"),
            twak_access_id=os.getenv("TWAK_ACCESS_ID"),
            twak_hmac_secret=os.getenv("TWAK_HMAC_SECRET"),
            cmc_api_key=os.getenv("CMC_API_KEY"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            llm_model=os.getenv("LLM_MODEL", "claude-opus-4-8"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_master_user_id=int(master_id) if master_id else None,
            bsc_rpc_url_override=os.getenv("BSC_RPC_URL"),
        )


@dataclass(frozen=True)
class Config:
    """Configuração não-secreta carregada de config.json."""

    user: dict[str, Any]
    dev_safety: dict[str, Any]
    hackathon: dict[str, Any]
    network: dict[str, Any]
    cmc: dict[str, Any]
    loop: dict[str, Any]
    secrets: Secrets = field(repr=False)

    # ── Atalhos de leitura usados em todo o projeto ──────────────────────────
    @property
    def position_size_pct(self) -> float:
        """Tamanho por trade (% da banca). O USUÁRIO escolhe (camada user, via
        Telegram); na ausência, cai no default seguro do dev_safety. Sempre limitado
        por max_position_pct e pelo stable disponível, no motor de risco."""
        user_pct = self.user.get("position_size_pct")
        return float(user_pct) if user_pct is not None else float(self.dev_safety["position_size_pct"])

    @property
    def user_position_size_pct(self) -> float:
        return self.position_size_pct

    @property
    def max_position_pct(self) -> float:
        return float(self.dev_safety.get("max_position_pct", 40.0))

    @property
    def prefilter_min_vol_change(self) -> float:
        return float(self.dev_safety.get("prefilter_min_vol_change_pct", 10.0))

    @property
    def max_slippage_pct(self) -> float:
        return float(self.dev_safety["max_slippage_pct"])

    @property
    def min_pool_liquidity_usd(self) -> float:
        """Profundidade mínima do pool (em USD, lado WBNB) p/ operar — protege contra
        pools rasos/manipuláveis mesmo dentro da whitelist."""
        return float(self.dev_safety.get("min_pool_liquidity_usd", 15000.0))

    @property
    def max_pool_share_pct(self) -> float:
        """Fração máxima do pool que um trade pode representar (limita impacto de preço)."""
        return float(self.dev_safety.get("max_pool_share_pct", 1.0))

    @property
    def max_entry_24h_pct(self) -> float:
        """Trava dura anti-topo: recusa ENTRAR num token que já subiu mais que isso em 24h
        (risco de blow-off top / reversão). Determinístico — não depende do LLM."""
        return float(self.dev_safety.get("max_entry_24h_pct", 25.0))

    @property
    def oracle_divergence_max_pct(self) -> float:
        return float(self.dev_safety["oracle_divergence_max_pct"])

    @property
    def trailing_trigger_pct(self) -> float:
        return float(self.dev_safety["trailing_stop_trigger_pct"])

    @property
    def trade_cooldown_seconds(self) -> int:
        return int(self.dev_safety["trade_cooldown_seconds"])

    @property
    def max_concurrent_positions(self) -> int:
        return int(self.dev_safety["max_concurrent_positions"])

    @property
    def min_position_usd(self) -> float:
        return float(self.dev_safety["min_position_usd"])

    @property
    def min_confidence_score(self) -> int:
        """Corte de confiança, sensível ao modo escolhido pelo usuário."""
        mode = str(self.user.get("mode", "conservative")).lower()
        key = f"min_confidence_score_{mode}"
        return int(self.dev_safety.get(key, self.dev_safety["min_confidence_score"]))

    @property
    def user_stop_loss_pct(self) -> float:
        return float(self.user["stop_loss_pct"])

    @property
    def user_take_profit_pct(self) -> float:
        """Lucro-alvo por trade (% acima da entrada). 0 = desativado (deixa o trailing correr)."""
        return float(self.user.get("take_profit_pct", 0.0) or 0.0)

    @property
    def drawdown_safety_pct(self) -> float:
        return float(self.hackathon["global_drawdown_safety_pct"])

    @property
    def drawdown_dq_pct(self) -> float:
        return float(self.hackathon["global_drawdown_dq_pct"])

    @property
    def daily_loss_cap_pct(self) -> float:
        """Limite de perda intradiária (% sobre o patrimônio do início do dia, UTC).
        Complementa o disjuntor de pico histórico: protege de um único dia ruim. 0 = off."""
        return float(self.dev_safety.get("daily_loss_cap_pct", 0.0) or 0.0)

    @property
    def max_hold_hours(self) -> float:
        """Saída por tempo: horas após as quais uma posição parada (sem trailing, PnL na
        faixa morta) é encerrada p/ liberar capital. 0 = desativado."""
        return float(self.dev_safety.get("max_hold_hours", 0.0) or 0.0)

    @property
    def stale_pnl_band_pct(self) -> float:
        """Faixa morta de PnL (±%) que caracteriza 'capital parado' na saída por tempo."""
        return float(self.dev_safety.get("stale_pnl_band_pct", 1.5))

    @property
    def stable_depeg_bps(self) -> float:
        """Desvio (em bps) da stable de trade em relação a $1 que dispara o depeg guard
        (bloqueia novas entradas + alerta). 100 bps = 1%. 0 = desativado."""
        return float(self.dev_safety.get("stable_depeg_bps", 0.0) or 0.0)

    @property
    def heartbeat_after_hours(self) -> float:
        return float(self.hackathon["heartbeat_after_hours"])

    @property
    def min_portfolio_usd(self) -> float:
        return float(self.hackathon["min_portfolio_usd"])

    @property
    def bsc_rpc_url(self) -> str:
        return self.secrets.bsc_rpc_url_override or str(self.network["bsc_rpc_url"])


def load_config(path: str | Path | None = None) -> Config:
    """Carrega .env + config.json e devolve um objeto Config validado."""
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return Config(
        user=data["user"],
        dev_safety=data["dev_safety"],
        hackathon=data["hackathon"],
        network=data["network"],
        cmc=data["cmc"],
        loop=data["loop"],
        secrets=Secrets.from_env(),
    )
