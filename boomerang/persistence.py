"""Persistência de estado do agente — sobrevive a reinício na semana ao vivo.

Salva posições, pico de patrimônio, último trade, contagem diária e config do
usuário em state/agent_state.json. Sem isso, um restart perderia o rastreio de
drawdown e o mínimo de trades — risco de desclassificação.

O diretório de estado é configurável via a variável de ambiente
``BOOMERANG_STATE_DIR`` (default: "state" na raiz do projeto). Os scripts de
teste apontam essa variável para um diretório temporário para NÃO sobrescrever o
estado real de produção. Os paths são resolvidos a cada chamada, então basta a
variável estar setada antes de chamar as funções (a ordem de import não importa).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _state_dir() -> Path:
    configured = os.environ.get("BOOMERANG_STATE_DIR")
    return Path(configured) if configured else ROOT / "state"


def _state_file() -> Path:
    return _state_dir() / "agent_state.json"


def _trades_file() -> Path:
    return _state_dir() / "trades.json"


def save_state(data: dict) -> None:
    state_file = _state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(state_file)  # escrita atômica


def load_state() -> dict | None:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def append_trade(record: dict) -> None:
    """Acrescenta um evento de trade ao histórico (para o dashboard)."""
    trades = load_trades()
    trades.append(record)
    trades_file = _trades_file()
    trades_file.parent.mkdir(parents=True, exist_ok=True)
    trades_file.write_text(json.dumps(trades[-200:], indent=2), encoding="utf-8")


def load_trades() -> list[dict]:
    try:
        return json.loads(_trades_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
