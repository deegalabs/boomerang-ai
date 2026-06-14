"""Console authentication via Sign-In with Ethereum (SIWE).

Flow:
  1. frontend requests a nonce (GET /api/auth/nonce)
  2. user signs, in the wallet, a message that includes that nonce
  3. frontend sends {address, message, signature} (POST /api/auth/verify)
  4. backend checks that the signature recovers the address AND that it is the
     OWNER_WALLET_ADDRESS. If ok, it issues a signed session cookie.

No new dependencies: uses eth_account (ships with web3) and standard-library hmac/secrets.
The session key is generated per process (sessions drop on restart — acceptable).
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256

from eth_account import Account
from eth_account.messages import encode_defunct

# SESSION_SECRET (env, hex) keeps sessions valid across restarts (Railway restarts
# the container); without it, one is generated per process (logins drop on every deploy).
_SESSION_SECRET = bytes.fromhex(_env) if (_env := os.getenv("SESSION_SECRET", "")) else secrets.token_bytes(32)
_SESSION_TTL = 12 * 3600                          # 12h
_NONCE_TTL = 600                                  # 10 min
_nonces: dict[str, float] = {}                    # nonce -> expires_at


def new_nonce() -> str:
    now = time.time()
    # clear expired nonces (avoids memory leak)
    for n, exp in list(_nonces.items()):
        if exp < now:
            _nonces.pop(n, None)
    nonce = secrets.token_hex(16)
    _nonces[nonce] = now + _NONCE_TTL
    return nonce


def build_message(address: str, nonce: str, domain: str) -> str:
    """Human-readable message the owner signs (SIWE-style, simple and clear)."""
    return (
        f"{domain} wants you to sign in to the Boomerang AI Console.\n\n"
        f"Wallet: {address}\n"
        f"Nonce: {nonce}\n\n"
        "Signing costs no gas and does not authorize any transaction."
    )


def _consume_nonce(nonce: str) -> bool:
    exp = _nonces.pop(nonce, None)
    return bool(exp and exp >= time.time())


def _recovered_ok(address: str, message: str, signature: str) -> bool:
    """True if the signature recovers `address` AND the message's nonce is valid."""
    if not address:
        return False
    nonce = None
    for line in message.splitlines():
        if line.strip().lower().startswith("nonce:"):
            nonce = line.split(":", 1)[1].strip()
    if not nonce or not _consume_nonce(nonce):
        return False
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception:  # noqa: BLE001
        return False
    return recovered.lower() == address.lower()


def verify_login(address: str, message: str, signature: str, owner: str) -> bool:
    """OWNER login: valid signature AND the signer is the OWNER_WALLET_ADDRESS."""
    if not owner or address.lower() != owner.lower():
        return False
    return _recovered_ok(address, message, signature)


def verify_signer(address: str, message: str, signature: str) -> bool:
    """DEMO login: any wallet that proves it owns the signature (no fixed owner)."""
    return _recovered_ok(address, message, signature)


# ── session (signed cookie) ──────────────────────────────────────────────────
def _sign(payload: bytes) -> str:
    sig = hmac.new(_SESSION_SECRET, payload, sha256).digest()
    return urlsafe_b64encode(payload).decode() + "." + urlsafe_b64encode(sig).decode()


def make_session(address: str) -> str:
    payload = json.dumps({"a": address.lower(), "exp": int(time.time()) + _SESSION_TTL}).encode()
    return _sign(payload)


def check_session(token: str | None) -> str | None:
    """Returns the address if the session is valid; otherwise None."""
    if not token or "." not in token:
        return None
    try:
        b_payload, b_sig = token.split(".", 1)
        payload = urlsafe_b64decode(b_payload)
        sig = urlsafe_b64decode(b_sig)
        if not hmac.compare_digest(hmac.new(_SESSION_SECRET, payload, sha256).digest(), sig):
            return None
        data = json.loads(payload)
        if data.get("exp", 0) < time.time():
            return None
        return data.get("a")
    except Exception:  # noqa: BLE001
        return None
