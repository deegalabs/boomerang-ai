"""Registra (uma vez) a identidade ERC-8004 do agente na BNB Chain.

Usa o BNB AI Agent SDK (`bnbagent`). O registro é gas-free (paymaster MegaFuel),
então não custa nada. A chave de identidade é gerada e guardada localmente em
`identity_wallet/` (git-ignorada) e é SEPARADA da carteira de trade do TWAK.

Uso:
  python scripts/register_identity.py                 # rede padrão (bsc-mainnet)
  python scripts/register_identity.py --network bsc-testnet
  python scripts/register_identity.py --force         # re-registra (novo agentId)

Tenta a rede pedida; se a mainnet falhar (ex.: paymaster recusar), cai para a
testnet automaticamente, a menos que --network seja explícito.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from boomerang.identity import bnb_agent

load_dotenv()


def _print_card(card: dict) -> None:
    print("\n  Identidade ERC-8004 registrada on-chain:")
    print(f"    agentId .... {card['agent_id']}")
    print(f"    rede ....... {card['network']} (chainId {card['chain_id']})")
    print(f"    carteira ... {card['address']}")
    print(f"    registry ... {card['registry']}")
    print(f"    tx ......... {card['tx']}")
    print(f"    BscScan .... {bnb_agent.explorer_tx(card)}")
    print(f"    8004scan ... {bnb_agent.scan_url(card)}")
    print(f"\n  Cartão salvo em: {bnb_agent.CARD_FILE}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default=None, help="bsc-mainnet | bsc-testnet")
    ap.add_argument("--force", action="store_true", help="re-registra mesmo se já houver cartão")
    args = ap.parse_args()

    if bnb_agent.is_registered() and not args.force:
        print("Já existe identidade registrada. Use --force para re-registrar.")
        _print_card(bnb_agent.load_card())
        return 0

    explicit = args.network is not None
    network = args.network or bnb_agent.DEFAULT_NETWORK
    print(f"Registrando identidade ERC-8004 em {network} (gas-free)...")
    try:
        card = bnb_agent.register(network=network, force=args.force)
    except Exception as exc:  # noqa: BLE001
        print(f"  Falhou em {network}: {exc}")
        if explicit or network == "bsc-testnet":
            return 1
        print("  Tentando fallback em bsc-testnet...")
        try:
            card = bnb_agent.register(network="bsc-testnet", force=args.force)
        except Exception as exc2:  # noqa: BLE001
            print(f"  Fallback testnet também falhou: {exc2}")
            return 1

    _print_card(card)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
