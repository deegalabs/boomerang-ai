"""Loading of configuration (config.json) and secrets (.env).

Clearly separates the three layers of rules:
  - user       → adjustable by the owner via Telegram
  - dev_safety → immutable safety laws (code)
  - hackathon  → fixed event rules

Secrets NEVER come from config.json; only from the environment (.env / OS variables).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional in production (secrets can come from the OS)
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Secrets:
    """Secrets coming from the environment. Never log or serialize."""

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
    """Non-secret configuration loaded from config.json."""

    user: dict[str, Any]
    dev_safety: dict[str, Any]
    hackathon: dict[str, Any]
    network: dict[str, Any]
    cmc: dict[str, Any]
    loop: dict[str, Any]
    secrets: Secrets = field(repr=False)

    # ── Read shortcuts used across the whole project ─────────────────────────
    @property
    def position_size_pct(self) -> float:
        """Size per trade (% of the bankroll). The USER chooses (user layer, via
        Telegram); in its absence, falls back to the safe default from dev_safety. Always
        limited by max_position_pct and by the available stable, in the risk engine."""
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
        """Minimum pool depth (in USD, WBNB side) to trade — protects against
        shallow/manipulable pools even within the whitelist."""
        return float(self.dev_safety.get("min_pool_liquidity_usd", 15000.0))

    @property
    def max_pool_share_pct(self) -> float:
        """Maximum fraction of the pool a single trade can represent (limits price impact)."""
        return float(self.dev_safety.get("max_pool_share_pct", 1.0))

    @property
    def max_entry_24h_pct(self) -> float:
        """Hard anti-top lock: refuses to ENTER a token that already rose more than this in 24h
        (blow-off top / reversal risk). Deterministic — does not depend on the LLM."""
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
        """Confidence cutoff, sensitive to the mode chosen by the user."""
        mode = str(self.user.get("mode", "conservative")).lower()
        key = f"min_confidence_score_{mode}"
        return int(self.dev_safety.get(key, self.dev_safety["min_confidence_score"]))

    @property
    def user_stop_loss_pct(self) -> float:
        return float(self.user["stop_loss_pct"])

    @property
    def user_take_profit_pct(self) -> float:
        """Take-profit per trade (% above entry). 0 = disabled (lets the trailing run)."""
        return float(self.user.get("take_profit_pct", 0.0) or 0.0)

    @property
    def drawdown_safety_pct(self) -> float:
        return float(self.hackathon["global_drawdown_safety_pct"])

    @property
    def drawdown_dq_pct(self) -> float:
        return float(self.hackathon["global_drawdown_dq_pct"])

    @property
    def daily_loss_cap_pct(self) -> float:
        """Intraday loss cap (% over the day's opening equity, UTC).
        Complements the all-time-peak circuit breaker: protects from a single bad day. 0 = off."""
        return float(self.dev_safety.get("daily_loss_cap_pct", 0.0) or 0.0)

    @property
    def max_hold_hours(self) -> float:
        """Time-based exit: hours after which a stalled position (no trailing, PnL in the
        dead band) is closed to free up capital. 0 = disabled."""
        return float(self.dev_safety.get("max_hold_hours", 0.0) or 0.0)

    @property
    def stale_pnl_band_pct(self) -> float:
        """PnL dead band (±%) that characterizes 'idle capital' in the time-based exit."""
        return float(self.dev_safety.get("stale_pnl_band_pct", 1.5))

    @property
    def stable_depeg_bps(self) -> float:
        """Deviation (in bps) of the trade stable from $1 that triggers the depeg guard
        (blocks new entries + alerts). 100 bps = 1%. 0 = disabled."""
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
    """Loads .env + config.json and returns a validated Config object."""
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
