"""Canal de eventos entre a Camada do Agente (Process B) e a Interface (Process A).

v1: barramento em processo (AlertBus). A costura permite trocar por IPC real
(socket/named pipe) na fase de endurecimento, mantendo a chave isolada do bot.
"""
from boomerang.ipc.events import Alert, AlertBus, AlertType

__all__ = ["Alert", "AlertBus", "AlertType"]
