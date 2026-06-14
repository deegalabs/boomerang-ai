"""Agent liveness signal, shared in-process between the agent thread and the
site thread (same process → same module object).

The agent beats (`beat()`) periodically in its event loop; `/healthz` reads the age
(`age_seconds()`). If it gets stale, the agent hung/died → `/healthz` returns 503 and
Railway restarts the container. Covers the DEADLOCK case (no exception), which the
railway_start restart loop does not catch.
"""
from __future__ import annotations

import time

_last_beat: float = time.time()


def beat() -> None:
    """Marks the agent as alive now. Called periodically by the agent's event loop."""
    global _last_beat
    _last_beat = time.time()


def age_seconds() -> float:
    """Seconds since the last beat. Grows without bound if the agent stops beating."""
    return time.time() - _last_beat
