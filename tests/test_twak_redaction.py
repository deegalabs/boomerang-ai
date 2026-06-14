"""The keystore password must never reach the logs (TWAK CLI arg redaction)."""
from __future__ import annotations

from boomerang.vault.twak_executor import TwakExecutor


def test_password_value_is_redacted():
    out = TwakExecutor._redact_args(["swap", "USDC", "ETH", "--password", "s3cr3t-pw"])
    assert "s3cr3t-pw" not in out
    assert "--password ***" in out


def test_addresses_and_hashes_are_dropped():
    out = TwakExecutor._redact_args(["transfer", "--to", "0xabc123def456", "--password", "pw"])
    assert "0xabc123def456" not in out
    assert "pw" not in out
    assert "transfer" in out and "--to" in out


def test_non_sensitive_args_kept():
    out = TwakExecutor._redact_args(["wallet", "portfolio", "--chains", "bsc"])
    assert out == "wallet portfolio --chains bsc"
