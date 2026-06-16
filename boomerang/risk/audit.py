"""Structured, append-only audit log (forensic trail).

Writes one JSON line per noteworthy event (rejections, halts, anomalies) to
``logs/audit.jsonl`` — non-sensitive, append-only, best-effort (never raises, never
blocks the trade loop). Complements the Telegram alerts with a machine-readable record.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

_log = logging.getLogger("boomerang.audit")
ROOT = Path(__file__).resolve().parent.parent.parent


def _audit_file() -> Path:
    base = os.environ.get("BOOMERANG_LOG_DIR")
    return (Path(base) if base else ROOT / "logs") / "audit.jsonl"


def audit(kind: str, **fields: object) -> None:
    """Append one event line. Best-effort: swallows any I/O error."""
    try:
        rec = {"ts": round(time.time(), 3), "kind": kind, **fields}
        path = _audit_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        _log.debug("audit write failed: %s", exc)
