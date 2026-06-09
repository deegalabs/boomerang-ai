"""Identidade on-chain do agente (ERC-8004) via BNB AI Agent SDK (pacote `bnbagent`).

O Boomerang AI registra uma identidade verificável no registro ERC-8004 da BNB
Chain. Isso dá ao agente um "passaporte" on-chain: um agentId, uma carteira de
identidade e um cartão de metadados que qualquer pessoa pode auditar (BscScan /
8004scan). É a integração do BNB AI Agent SDK do projeto.

Decisões de segurança e custo:
  - A chave de IDENTIDADE é SEPARADA da carteira de TRADE. A carteira que move o
    dinheiro vive no keystore do TWAK e o SDK nunca a toca. Assim, registrar a
    identidade não expõe nem arrisca a custódia dos fundos.
  - O registro é gas-free na BNB Chain (paymaster MegaFuel), então não há custo.
  - O keystore da identidade fica em `identity_wallet/` (git-ignorado). O cartão
    público de prova (agentId, tx, endereço) fica versionado em `agent_card.json`,
    porque é tudo dado público on-chain e serve de evidência para os juízes.

Uso:
  - registro (uma vez):   python scripts/register_identity.py
  - leitura (no agente):  load_card() -> dict | None     (NÃO precisa de senha)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
IDENTITY_DIR = ROOT / "identity_wallet"                      # keystore (git-ignorado)
CARD_FILE = Path(__file__).resolve().parent / "agent_card.json"  # prova pública (versionada)

DEFAULT_NETWORK = os.getenv("BNB_IDENTITY_NETWORK", "bsc-mainnet")
_CHAIN_ID = {"bsc-mainnet": 56, "bsc-testnet": 97}
_SCAN_TX = {"bsc-mainnet": "https://bscscan.com/tx/",
            "bsc-testnet": "https://testnet.bscscan.com/tx/"}


def _password() -> str:
    """Senha do keystore de identidade. Dedicada, com fallback na do TWAK."""
    return os.getenv("BNB_IDENTITY_PASSWORD") or os.getenv("WALLET_PASSWORD") or ""


def explorer_tx(card: dict) -> str:
    base = _SCAN_TX.get(card.get("network", ""), _SCAN_TX["bsc-mainnet"])
    return base + card.get("tx", "") if card.get("tx") else ""


def scan_url(card: dict) -> str:
    """Explorador público de agentes ERC-8004 (8004scan). O deep-link por agente
    não é estável (SPA), então apontamos para o índice — a prova dura é a BscScan."""
    return "https://8004scan.io/agents"


def registry_url(card: dict) -> str:
    """Contrato do registro ERC-8004 na BscScan (prova secundária, auditável)."""
    base = "https://bscscan.com/address/" if card.get("chain_id") == 56 else "https://testnet.bscscan.com/address/"
    return base + card.get("registry", "") if card.get("registry") else ""


def load_card() -> dict | None:
    """Lê o cartão de identidade do disco (sem precisar de senha/keystore)."""
    try:
        return json.loads(CARD_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def is_registered() -> bool:
    card = load_card()
    return bool(card and card.get("agent_id") is not None)


def summary() -> dict:
    """Resumo enxuto para o snapshot do agente e para a web. Sempre seguro de expor."""
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
    """Registra (uma vez) a identidade ERC-8004 do agente e grava o cartão de prova.

    Idempotente: se já houver cartão registrado e `force` for falso, devolve o
    existente sem tocar a blockchain. Importa o SDK só aqui (dependência pesada).
    """
    if not force and is_registered():
        return load_card()  # type: ignore[return-value]

    from bnbagent import AgentEndpoint, ERC8004Agent, EVMWalletProvider

    pw = password or _password()
    if not pw:
        raise RuntimeError(
            "Senha do keystore de identidade ausente. Defina BNB_IDENTITY_PASSWORD "
            "(ou WALLET_PASSWORD) no .env."
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
        raise RuntimeError(f"Registro ERC-8004 falhou: {result}")

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
