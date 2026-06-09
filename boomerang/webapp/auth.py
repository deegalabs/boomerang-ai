"""Autenticação do Console por Sign-In with Ethereum (SIWE).

Fluxo:
  1. front pede um nonce (GET /api/auth/nonce)
  2. usuário assina, na carteira, uma mensagem que inclui esse nonce
  3. front envia {address, message, signature} (POST /api/auth/verify)
  4. backend confere que a assinatura recupera o endereço E que ele é o
     OWNER_WALLET_ADDRESS. Se ok, emite um cookie de sessão assinado.

Sem dependências novas: usa eth_account (vem com web3) e hmac/secrets do padrão.
A chave da sessão é gerada por processo (sessões caem ao reiniciar — aceitável).
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

_SESSION_SECRET = secrets.token_bytes(32)        # por processo
_SESSION_TTL = 12 * 3600                          # 12h
_NONCE_TTL = 600                                  # 10 min
_nonces: dict[str, float] = {}                    # nonce -> expira_em


def new_nonce() -> str:
    now = time.time()
    # limpa nonces expirados (evita vazamento de memória)
    for n, exp in list(_nonces.items()):
        if exp < now:
            _nonces.pop(n, None)
    nonce = secrets.token_hex(16)
    _nonces[nonce] = now + _NONCE_TTL
    return nonce


def build_message(address: str, nonce: str, domain: str) -> str:
    """Mensagem legível que o dono assina (estilo SIWE, simples e claro)."""
    return (
        f"{domain} quer que você entre no Console do Boomerang AI.\n\n"
        f"Carteira: {address}\n"
        f"Nonce: {nonce}\n\n"
        "Assinar não custa gás e não autoriza nenhuma transação."
    )


def _consume_nonce(nonce: str) -> bool:
    exp = _nonces.pop(nonce, None)
    return bool(exp and exp >= time.time())


def _recovered_ok(address: str, message: str, signature: str) -> bool:
    """True se a assinatura recupera o `address` E o nonce da mensagem é válido."""
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
    """Login do DONO: assinatura válida E o signatário é o OWNER_WALLET_ADDRESS."""
    if not owner or address.lower() != owner.lower():
        return False
    return _recovered_ok(address, message, signature)


def verify_signer(address: str, message: str, signature: str) -> bool:
    """Login do DEMO: qualquer carteira que prove ser dona da assinatura (sem dono fixo)."""
    return _recovered_ok(address, message, signature)


# ── sessão (cookie assinado) ─────────────────────────────────────────────────
def _sign(payload: bytes) -> str:
    sig = hmac.new(_SESSION_SECRET, payload, sha256).digest()
    return urlsafe_b64encode(payload).decode() + "." + urlsafe_b64encode(sig).decode()


def make_session(address: str) -> str:
    payload = json.dumps({"a": address.lower(), "exp": int(time.time()) + _SESSION_TTL}).encode()
    return _sign(payload)


def check_session(token: str | None) -> str | None:
    """Retorna o endereço se a sessão for válida; senão None."""
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
