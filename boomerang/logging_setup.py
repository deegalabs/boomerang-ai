"""Logging central. Nunca loga segredos; mascara chaves/seeds por precaução."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Chaves privadas cruas (32 bytes) e tokens de bot do Telegram (id:segredo).
_SECRET_PATTERNS = [
    re.compile(r"0x[a-fA-F0-9]{64}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
]
# Bibliotecas que logam URLs/headers com segredos (o httpx loga a URL do Telegram
# com o token). Mantemos em WARNING para não vazar nada nos logs.
_NOISY = ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3", "web3", "anthropic")


class _SecretMaskingFilter(logging.Filter):
    """Mascara chaves privadas e tokens que por acaso entrem na mensagem."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pat in _SECRET_PATTERNS:
                record.msg = pat.sub("<REDACTED>", record.msg)
        return True


def setup_logging(name: str = "boomerang", level: int = logging.INFO) -> logging.Logger:
    # Silencia bibliotecas que vazam segredos na URL/headers (sempre, idempotente).
    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger(name)
    if logger.handlers:  # idempotente
        return logger
    logger.setLevel(level)

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
