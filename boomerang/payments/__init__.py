"""x402 (pay-per-call) payments signed by the BNB AI Agent SDK.

Standalone showcase / proof-of-capability (see scripts/x402_pay.py) — NOT the runtime
data path (the brain uses CMC REST; runtime x402 goes through the twak CLI)."""
from boomerang.payments.x402_cmc import (
    CMC_X402_URL,
    X402Error,
    call_tool,
    decode_challenge,
    list_tools,
    make_signer,
    open_signer,
    pick_accept,
    signing_policy,
)

__all__ = [
    "CMC_X402_URL",
    "X402Error",
    "call_tool",
    "decode_challenge",
    "list_tools",
    "make_signer",
    "open_signer",
    "pick_accept",
    "signing_policy",
]
