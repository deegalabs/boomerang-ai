"""Events/alerts emitted by the agent and consumed by the interface."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable


class AlertType(str, Enum):
    STARTED = "STARTED"
    PAUSED = "PAUSED"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    TRADE_OPENED = "TRADE_OPENED"
    TRADE_CLOSED = "TRADE_CLOSED"
    REJECTED = "REJECTED"
    HEARTBEAT = "HEARTBEAT"
    WITHDRAWN = "WITHDRAWN"
    ERROR = "ERROR"
    SCAN = "SCAN"            # summary of each scan cycle
    DATA_ERROR = "DATA_ERROR"  # failure to fetch market data (CMC/x402)


@dataclass
class Alert:
    type: AlertType
    title: str
    detail: str = ""
    data: dict = field(default_factory=dict)


Subscriber = Callable[[Alert], Awaitable[None] | None]


class AlertBus:
    """Simple async pub/sub. Subscribers can be sync or async."""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []

    def subscribe(self, cb: Subscriber) -> None:
        self._subs.append(cb)

    async def emit(self, alert: Alert) -> None:
        for cb in self._subs:
            res = cb(alert)
            if inspect.isawaitable(res):
                await res
