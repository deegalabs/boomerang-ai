"""On-chain agent identity (ERC-8004) via BNB AI Agent SDK (`bnbagent` package).

Boomerang AI registers a verifiable identity in the ERC-8004 registry of the BNB
Chain. This gives the agent an on-chain "passport": an agentId, an identity
wallet and a metadata card that anyone can audit (BscScan /
8004scan). It is the project's BNB AI Agent SDK integration.

Security and cost decisions:
  - The IDENTITY key is SEPARATE from the TRADE wallet. The wallet that moves the
    money lives in the TWAK keystore and the SDK never touches it. So, registering the
    identity neither exposes nor risks the custody of the funds.
  - Registration is gas-free on the BNB Chain (MegaFuel paymaster), so there is no cost.
  - The identity keystore lives in `identity_wallet/` (git-ignored). The public
    proof card (agentId, tx, address) is versioned in `agent_card.json`,
    because it is all public on-chain data and serves as evidence for the judges.

Usage:
  - registration (once):   python scripts/register_identity.py
  - reading (in the agent): load_card() -> dict | None     (does NOT need a password)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
IDENTITY_DIR = ROOT / "identity_wallet"                      # keystore (git-ignored)
CARD_FILE = Path(__file__).resolve().parent / "agent_card.json"  # public proof (versioned)

DEFAULT_NETWORK = os.getenv("BNB_IDENTITY_NETWORK", "bsc-mainnet")
_CHAIN_ID = {"bsc-mainnet": 56, "bsc-testnet": 97}
_SCAN_TX = {"bsc-mainnet": "https://bscscan.com/tx/",
            "bsc-testnet": "https://testnet.bscscan.com/tx/"}


def _password() -> str:
    """Identity keystore password. Dedicated, with a fallback to the TWAK one."""
    return os.getenv("BNB_IDENTITY_PASSWORD") or os.getenv("WALLET_PASSWORD") or ""


def explorer_tx(card: dict) -> str:
    base = _SCAN_TX.get(card.get("network", ""), _SCAN_TX["bsc-mainnet"])
    return base + card.get("tx", "") if card.get("tx") else ""


def scan_url(card: dict) -> str:
    """Public explorer of ERC-8004 agents (8004scan). The per-agent deep-link
    is not stable (SPA), so we point to the index — the hard proof is BscScan."""
    return "https://8004scan.io/agents"


def registry_url(card: dict) -> str:
    """ERC-8004 registry contract on BscScan (secondary, auditable proof)."""
    base = "https://bscscan.com/address/" if card.get("chain_id") == 56 else "https://testnet.bscscan.com/address/"
    return base + card.get("registry", "") if card.get("registry") else ""


def load_card() -> dict | None:
    """Reads the identity card from disk (without needing a password/keystore)."""
    try:
        return json.loads(CARD_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def is_registered() -> bool:
    card = load_card()
    return bool(card and card.get("agent_id") is not None)


def summary() -> dict:
    """Lean summary for the agent snapshot and for the web. Always safe to expose."""
    card = load_card()
    if not card or card.get("agent_id") is None:
        return {"registered": False}
    return {
        "registered": True,
        "agent_id": card["agent_id"],
        "address": card.get("address", ""),
        "network": card.get("network", ""),
        "chain_id": card.get("chain_id"),
        "registry": card.get("registry", ""),
        "tx": card.get("tx", ""),
        "explorer": explorer_tx(card),
        "registry_url": registry_url(card),
        "scan": scan_url(card),
        "name": card.get("name", "Boomerang AI"),
    }


def register(
    *,
    network: str = DEFAULT_NETWORK,
    name: str = "Boomerang AI",
    description: str = (
        "Autonomous BNB Chain trading agent. Reads CoinMarketCap attention, decides "
        "with Claude, and executes self-custody swaps via Trust Wallet Agent Kit."
    ),
    endpoint: str = "https://boomerang.deegalabs.ai/erc8183/status",
    image: str = "",
    password: str | None = None,
    force: bool = False,
) -> dict:
    """Registers (once) the agent's ERC-8004 identity and writes the proof card.

    Idempotent: if there is already a registered card and `force` is false, returns the
    existing one without touching the blockchain. Imports the SDK only here (heavy dependency).
    """
    if not force and is_registered():
        return load_card()  # type: ignore[return-value]

    from bnbagent import AgentEndpoint, ERC8004Agent, EVMWalletProvider

    pw = password or _password()
    if not pw:
        raise RuntimeError(
            "Identity keystore password missing. Set BNB_IDENTITY_PASSWORD "
            "(or WALLET_PASSWORD) in .env."
        )
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True)

    wallet = EVMWalletProvider(password=pw, persist=True, wallets_dir=str(IDENTITY_DIR))
    sdk = ERC8004Agent(wallet_provider=wallet, network=network)

    agent_uri = sdk.generate_agent_uri(
        name=name,
        description=description,
        endpoints=[AgentEndpoint(name="ERC-8183", endpoint=endpoint,
                                 version="0.1.0", capabilities=["trading"])],
        image=image or None,
    )
    result = sdk.register_agent(agent_uri=agent_uri)
    if not result.get("success"):
        raise RuntimeError(f"ERC-8004 registration failed: {result}")

    card = {
        "agent_id": result.get("agentId"),
        "tx": result.get("transactionHash"),
        "address": wallet.address,
        "network": network,
        "chain_id": _CHAIN_ID.get(network),
        "registry": sdk.contract_address,
        "name": name,
        "description": description,
        "endpoint": endpoint,
        "agent_uri": agent_uri,
        "registered_at": int(time.time()),
        "gas_free": True,
    }
    CARD_FILE.write_text(json.dumps(card, indent=2), encoding="utf-8")
    return card


def commit_prediction(pred: dict, *, password: str | None = None) -> dict | None:
    """SEALS a trade's reasoning ON-CHAIN (ERC-8004) BEFORE execution — anti-fabrication
    proof: the why is recorded before the RESULT exists. Best-effort and
    GAS-FREE (MegaFuel paymaster). Never interrupts the trade (the caller doesn't wait).

    pred: {symbol, score, volatility, ch24, rationale, ts}. Returns {key, hash, tx} or None.
    """
    card = load_card()
    if not card or not card.get("agent_id"):
        return None
    try:
        import json as _json

        from web3 import Web3

        from bnbagent import ERC8004Agent, EVMWalletProvider
        pw = password or _password()
        if not pw:
            return None
        # keccak256 hash of the canonical reasoning (same Ethereum/ERC-8004 convention).
        canonical = "|".join(str(pred.get(k, "")) for k in
                             ("symbol", "score", "volatility", "ch24", "rationale", "ts"))
        h = Web3.keccak(text=canonical).hex()
        wallet = EVMWalletProvider(password=pw, persist=True, wallets_dir=str(IDENTITY_DIR))
        sdk = ERC8004Agent(wallet_provider=wallet, network=card.get("network", DEFAULT_NETWORK))
        key = f"pred_{pred.get('ts')}_{pred.get('symbol')}"[:60]
        value = _json.dumps({"hash": h, "sym": pred.get("symbol"), "dir": "BUY",
                             "conf": pred.get("score"), "vol": pred.get("volatility"),
                             "ts": pred.get("ts")}, separators=(",", ":"))[:480]
        result = sdk.set_metadata(agent_id=card["agent_id"], key=key, value=value)
        if isinstance(result, dict) and result.get("success"):
            return {"key": key, "hash": h, "tx": result.get("transactionHash")}
        return None
    except Exception:  # noqa: BLE001 — accountability is an extra layer; never takes down the trade
        return None


def publish_risk_state(state: dict, *, password: str | None = None) -> dict | None:
    """ON-CHAIN CIRCUIT BREAKER (ERC-8004 attestation): records the state of the drawdown
    circuit breaker as verifiable metadata (key 'risk_state') — peak, equity,
    drawdown and whether it is locked. Any auditor reads on-chain that the lock EXISTS and
    is ACTIVE; on a halt, the on-chain proof that the killswitch fired remains.

    Mirrors the on-chain RiskGovernor (equity keeper) using our gas-free ERC-8004
    infra (MegaFuel paymaster) — no new contract to deploy. Best-effort:
    any failure returns None and NEVER interrupts trading. Call sparingly
    (it is a transaction): ~1x/hour on the heartbeat, and ALWAYS on the halt event.

    state: {peak, equity, drawdown_bps, daily_bps, max_bps, daily_cap_bps, halted, ts}.
    """
    card = load_card()
    if not card or not card.get("agent_id"):
        return None
    try:
        from bnbagent import ERC8004Agent, EVMWalletProvider

        pw = password or _password()
        if not pw:
            return None
        wallet = EVMWalletProvider(password=pw, persist=True, wallets_dir=str(IDENTITY_DIR))
        sdk = ERC8004Agent(wallet_provider=wallet, network=card.get("network", DEFAULT_NETWORK))
        value = json.dumps(state, separators=(",", ":"))[:480]
        result = sdk.set_metadata(agent_id=card["agent_id"], key="risk_state", value=value)
        return result if isinstance(result, dict) and result.get("success") else None
    except Exception:  # noqa: BLE001 — on-chain circuit breaker is extra proof; never takes down the agent
        return None


def publish_track_record(stats: dict, *, password: str | None = None) -> dict | None:
    """SKILL On-chain reputation: records the agent's performance history as
    verifiable ERC-8004 metadata (key 'track_record'). Best-effort — any
    failure (gas, network) returns None and NEVER interrupts trading. Call sparingly
    (it is a transaction): the agent does this at most ~1x/hour."""
    card = load_card()
    if not card or not card.get("agent_id"):
        return None
    try:
        from bnbagent import ERC8004Agent, EVMWalletProvider

        pw = password or _password()
        if not pw:
            return None
        wallet = EVMWalletProvider(password=pw, persist=True, wallets_dir=str(IDENTITY_DIR))
        sdk = ERC8004Agent(wallet_provider=wallet, network=card.get("network", DEFAULT_NETWORK))
        value = json.dumps(stats, separators=(",", ":"))[:480]
        result = sdk.set_metadata(agent_id=card["agent_id"], key="track_record", value=value)
        return result if isinstance(result, dict) and result.get("success") else None
    except Exception:  # noqa: BLE001 — reputation is showcase; never takes down the agent
        return None
