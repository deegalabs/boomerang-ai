"""Logging central. Nunca loga segredos; mascara chaves/seeds por precaução."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SECRET_PATTERN = re.compile(r"0x[a-fA-F0-9]{64}")  # chaves privadas cruas


class _SecretMaskingFilter(logging.Filter):
    """Mascara qualquer coisa que pareça uma chave privada de 32 bytes."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_PATTERN.sub("0x<REDACTED>", record.msg)
        return True


def setup_logging(name: str = "boomerang", level: int = logging.INFO) -> logging.Logger:
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
