"""State-file integrity (HMAC-SHA256).

A defense-in-depth layer: the persisted state (positions, peak equity, daily count) is
signed so external tampering is detected before the agent trades on it. Opt-in and
**backward-compatible** — without ``STATE_HMAC_SECRET`` set, signing/verification are
no-ops, so existing deploys are unaffected.
"""
from __future__ import annotations

import hashlib
import hmac
import os


def _secret() -> bytes:
    return os.getenv("STATE_HMAC_SECRET", "").encode()


def enabled() -> bool:
    return bool(_secret())


def sign(raw: bytes) -> str:
    """HMAC of the raw state bytes, or "" when no secret is configured."""
    return hmac.new(_secret(), raw, hashlib.sha256).hexdigest() if enabled() else ""


def verify(raw: bytes, sig: str | None) -> bool:
    """True if the signature matches — or if integrity is disabled / there is no prior
    signature yet (first run / legacy state). Constant-time comparison."""
    if not enabled():
        return True
    if not sig:
        return True  # no sig recorded yet; the next save will write one
    return hmac.compare_digest(sign(raw), sig)
