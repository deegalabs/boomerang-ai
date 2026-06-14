"""Print the base64 of the ERC-8004 identity keystore (for IDENTITY_WALLET_JSON_B64 in prod).

The identity keystore (identity_wallet/<addr>.json) is ENCRYPTED and git-ignored. Production
materializes it from this base64 secret — same pattern as the trade wallet's TWAK_WALLET_JSON_B64.
The file is useless without BNB_IDENTITY_PASSWORD/WALLET_PASSWORD to decrypt it, and the identity
wallet holds NO funds, so this is low-risk to handle. No password or SDK is needed here.

Usage:
    python scripts/identity_keystore_b64.py            # prints base64 to stdout
    python scripts/identity_keystore_b64.py | clip     # (Windows) copy straight to clipboard
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDENTITY_DIR = ROOT / "identity_wallet"
CARD = ROOT / "boomerang" / "identity" / "agent_card.json"


def main() -> None:
    addr = None
    try:
        addr = json.loads(CARD.read_text(encoding="utf-8")).get("address")
    except Exception:  # noqa: BLE001
        pass
    keystore = (IDENTITY_DIR / f"{addr}.json") if addr else None
    if not keystore or not keystore.exists():
        files = list(IDENTITY_DIR.glob("*.json"))
        if not files:
            sys.exit(f"No identity keystore found in {IDENTITY_DIR}.")
        keystore = files[0]
    b64 = base64.b64encode(keystore.read_bytes()).decode()
    print(f"# keystore: {keystore.name} ({len(b64)} b64 chars)", file=sys.stderr)
    print("# set this value as the IDENTITY_WALLET_JSON_B64 secret in production:", file=sys.stderr)
    print(b64)


if __name__ == "__main__":
    main()
