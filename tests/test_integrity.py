"""State-file integrity (HMAC) — opt-in, backward-compatible, tamper-detecting."""
from __future__ import annotations

import json

from boomerang import persistence
from boomerang.risk import integrity


def test_disabled_without_secret(monkeypatch):
    monkeypatch.delenv("STATE_HMAC_SECRET", raising=False)
    assert integrity.enabled() is False
    assert integrity.sign(b"x") == ""
    assert integrity.verify(b"x", "anything") is True   # disabled → always accept


def test_sign_and_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("STATE_HMAC_SECRET", "topsecret")
    sig = integrity.sign(b"hello")
    assert sig and integrity.verify(b"hello", sig) is True
    assert integrity.verify(b"hello-tampered", sig) is False   # tamper detected
    assert integrity.verify(b"hello", "") is True              # no prior sig → accept


def test_persistence_integrity(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_HMAC_SECRET", "k")
    monkeypatch.setenv("BOOMERANG_STATE_DIR", str(tmp_path))
    persistence.save_state({"equity_usd": 100.0, "peak_equity": 120.0})
    assert persistence.verify_state_integrity() is True
    # tamper the file on disk → integrity must fail
    f = tmp_path / "agent_state.json"
    f.write_text(json.dumps({"equity_usd": 999.0, "peak_equity": 1.0}), encoding="utf-8")
    assert persistence.verify_state_integrity() is False


def test_no_state_file_is_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_HMAC_SECRET", "k")
    monkeypatch.setenv("BOOMERANG_STATE_DIR", str(tmp_path / "empty"))
    assert persistence.verify_state_integrity() is True        # nothing to verify yet
