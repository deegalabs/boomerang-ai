"""Export the ERC-8004 identity wallet private key (to set BNB_IDENTITY_PRIVATE_KEY in prod).

The identity keystore (identity_wallet/) is git-ignored and not shipped to the container,
so production needs the private key of the wallet that OWNS the agentId injected as a secret.
Run this LOCALLY (where the keystore exists), copy the printed key into the production secret
BNB_IDENTITY_PRIVATE_KEY, and clear your terminal afterwards.

Usage:
    python scripts/export_identity_key.py

Needs BNB_IDENTITY_PASSWORD (or WALLET_PASSWORD) in the environment / .env.
The identity wallet holds NO funds (gas-free via MegaFuel), so the key only authorizes
on-chain metadata writes — not the trade funds (those live in the separate TWAK keystore).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from boomerang.identity.bnb_agent import IDENTITY_DIR, _password, load_card  # noqa: E402


def main() -> None:
    pw = _password()
    if not pw:
        sys.exit("Missing BNB_IDENTITY_PASSWORD / WALLET_PASSWORD.")
    if not IDENTITY_DIR.exists():
        sys.exit(f"No identity keystore at {IDENTITY_DIR}. Register first.")

    from bnbagent import EVMWalletProvider

    wallet = EVMWalletProvider(password=pw, persist=True, wallets_dir=str(IDENTITY_DIR))
    card = load_card() or {}
    expected = card.get("address")
    if expected and wallet.address.lower() != expected.lower():
        print(f"WARNING: loaded wallet {wallet.address} != registered {expected}", file=sys.stderr)

    print(f"# identity wallet: {wallet.address} (agentId {card.get('agent_id')})", file=sys.stderr)
    print("# set this as BNB_IDENTITY_PRIVATE_KEY in production:", file=sys.stderr)
    print(wallet.export_private_key())


if __name__ == "__main__":
    main()
