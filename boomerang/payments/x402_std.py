"""x402 in the Google A2A x402 standard shape.

Our runtime x402 payment is twak-signed and load-bearing in the loop (see
``brain/cmc_analyzer.gather_x402_derivatives`` + ``vault/twak_executor.x402_request``).
This module is an **additive** conformance layer: it expresses that same payment using
the field names of the **A2A x402 extension** (``google-agentic-commerce/a2a-x402``) —
the standard metadata keys, the ``exact`` scheme, and a settlement receipt — so the
integration is recognizable as following an emerging Google/standard spec. Pure and
stdlib-only; nothing here moves money (the real settlement happens via TWAK).
"""
from __future__ import annotations

# Standard metadata keys (A2A x402 extension)
STATUS_KEY = "x402.payment.status"
REQUIRED_KEY = "x402.payment.required"
RECEIPTS_KEY = "x402.payment.receipts"
STATUS_COMPLETED = "payment-completed"

# USDC on Base (the asset our agent signs over via EIP-3009)
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def payment_requirements(max_amount_atomic: str = "10000", network: str = "base",
                         asset: str = "USDC", resource: str = "CMC Agent Hub derivatives") -> dict:
    """An ``exact``-scheme PaymentRequirements (10000 atomic = $0.01 USDC, 6 decimals)."""
    return {
        "scheme": "exact",
        "network": network,
        "asset": asset,
        "assetAddress": USDC_BASE if asset == "USDC" and network == "base" else "",
        "maxAmountRequired": max_amount_atomic,
        "resource": resource,
        "description": "Pay-per-call for premium CoinMarketCap Agent Hub derivatives",
    }


def settle_receipt(tx_hash: str, network: str = "base", amount_atomic: str = "10000",
                   asset: str = "USDC") -> dict:
    """A completed-payment receipt in the standard ``receipts`` shape."""
    return {
        STATUS_KEY: STATUS_COMPLETED,
        RECEIPTS_KEY: [{
            "network": network, "asset": asset, "amount": amount_atomic,
            "transaction": tx_hash,
            "explorer": f"https://basescan.org/tx/{tx_hash}",
        }],
    }


def descriptor(last_tx: str | None = None, *, reference: bool = False) -> dict:
    """The agent's x402 integration described in standard terms (for /api/x402-status).

    ``reference=True`` marks ``last_tx`` as a verified *example* settlement (not a live feed):
    in-loop settlements recur ~1x/hour but their tx hashes aren't surfaced here, so we show one
    real, independently-verifiable Base tx as the conformance proof — labelled as such, honestly."""
    out = {
        "spec": "A2A x402 extension (google-agentic-commerce) · exact scheme",
        "in_loop": True,
        "frequency": "~1x/hour",
        "signing": "EIP-3009 USDC on Base, signed locally via Trust Wallet Agent Kit (self-custody)",
        "payment_required": payment_requirements(),
    }
    if last_tx:
        out["last_settlement"] = settle_receipt(last_tx)
        if reference:
            out["last_settlement"]["note"] = (
                "verified reference settlement (a real past on-chain tx); "
                "in-loop settlements recur ~1x/hour and are not individually surfaced here")
    return out
