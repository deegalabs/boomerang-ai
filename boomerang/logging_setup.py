"""Central logging. Never logs secrets; masks keys/seeds as a precaution."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Raw private keys (32 bytes) and Telegram bot tokens (id:secret).
_SECRET_PATTERNS = [
    re.compile(r"0x[a-fA-F0-9]{64}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
]
# Libraries that log URLs/headers with secrets (httpx logs the Telegram URL
# with the token). We keep them at WARNING so nothing leaks into the logs.
_NOISY = ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3", "web3", "anthropic")


class _SecretMaskingFilter(logging.Filter):
    """Masks private keys and tokens that may end up in the message."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pat in _SECRET_PATTERNS:
                record.msg = pat.sub("<REDACTED>", record.msg)
        return True


def setup_logging(name: str = "boomerang", level: int = logging.INFO) -> logging.Logger:
    # Silence libraries that leak secrets in the URL/headers (always, idempotent).
    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger(name)
    if logger.handlers:  # idempotent
        return logger
    logger.setLevel(level)
    # Don't bubble up to the root logger (railway_start's basicConfig) — otherwise every
    # message is emitted TWICE (once by our handler, once by root's default handler).
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.addFilter(_SecretMaskingFilter())
    logger.addHandler(stream)

    logs_dir = ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(logs_dir / f"{name}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(_SecretMaskingFilter())
    logger.addHandler(file_handler)

    return logger
