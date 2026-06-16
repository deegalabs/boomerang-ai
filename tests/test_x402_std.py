"""x402 standard-shape conformance layer (A2A x402 extension format)."""
from __future__ import annotations

from boomerang.payments import x402_std as x


def test_payment_requirements_exact_scheme():
    pr = x.payment_requirements()
    assert pr["scheme"] == "exact"
    assert pr["network"] == "base" and pr["asset"] == "USDC"
    assert pr["assetAddress"] == x.USDC_BASE
    assert pr["maxAmountRequired"] == "10000"   # $0.01 USDC (6 decimals)


def test_settle_receipt_shape():
    r = x.settle_receipt("0xabc")
    assert r[x.STATUS_KEY] == x.STATUS_COMPLETED
    rec = r[x.RECEIPTS_KEY][0]
    assert rec["transaction"] == "0xabc"
    assert rec["explorer"].endswith("/tx/0xabc")
    assert rec["network"] == "base"


def test_descriptor_with_and_without_tx():
    d0 = x.descriptor()
    assert d0["in_loop"] is True and "payment_required" in d0
    assert "last_settlement" not in d0
    d1 = x.descriptor(last_tx="0xfeed")
    assert d1["last_settlement"][x.RECEIPTS_KEY][0]["transaction"] == "0xfeed"
