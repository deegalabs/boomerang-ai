"""Sinal de vida do agente (liveness), compartilhado em-processo entre a thread do
agente e a thread do site (mesmo processo → mesmo objeto de módulo).

O agente bate (`beat()`) periodicamente no seu event loop; o `/healthz` lê a idade
(`age_seconds()`). Se ficar velho, o agente travou/morreu → o /healthz devolve 503 e
a Railway reinicia o container. Cobre o caso de DEADLOCK (sem exceção), que o loop de
restart do railway_start não pega.
"""
from __future__ import annotations

import time

_last_beat: float = time.time()


def beat() -> None:
    """Marca o agente como vivo agora. Chamado periodicamente pelo event loop do agente."""
    global _last_beat
    _last_beat = time.time()


def age_seconds() -> float:
    """Segundos desde o último beat. Cresce sem limite se o agente parar de bater."""
    return time.time() - _last_beat
