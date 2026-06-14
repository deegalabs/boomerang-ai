"""Agent state persistence — survives a restart during the live week.

Saves positions, peak equity, last trade, daily count and the user's config in
state/agent_state.json. Without this, a restart would lose the drawdown tracking
and the minimum number of trades — risk of disqualification.

The state directory is configurable via the ``BOOMERANG_STATE_DIR`` environment
variable (default: "state" at the project root). The test scripts point this
variable at a temporary directory so as NOT to overwrite the real production
state. The paths are resolved on every call, so it is enough for the variable to
be set before calling the functions (import order does not matter).
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
    tmp.replace(state_file)  # atomic write


def load_state() -> dict | None:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def append_trade(record: dict) -> None:
    """Appends a trade event to the history (for the dashboard)."""
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
