"""Valida persistencia: salva estado e restaura em um novo agente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _isolate_state  # noqa: F401  isola o estado num tempdir ANTES de importar boomerang

from boomerang.agent import BoomerangAgent
from boomerang.config import load_config
from boomerang.ipc import AlertBus
from boomerang.risk import RiskEngine
from boomerang.types import AgentState, Position


class Stub:  # validator/executor/analyzer nao sao usados na persistencia
    def __getattr__(self, _): return lambda *a, **k: None


def make():
    cfg = load_config()
    return BoomerangAgent(cfg, validator=Stub(), executor=Stub(), analyzer=Stub(),
                          risk=RiskEngine(cfg, 100.0), alerts=AlertBus())


def main() -> int:
    a1 = make()
    a1.configure(token_focus=["ETH", "LINK"], stop_loss_pct=3.0, mode="aggressive")
    a1._risk.update_equity(150.0)            # pico sobe
    a1._risk.record_trade(12345.0)
    a1.positions.append(Position(symbol="LINK", token_address="0xabc", entry_price=10.0,
                                 amount_usd=5.0, qty=0.5, stop_loss_price=9.5))
    a1.state = AgentState.IN_POSITION
    a1._save()
    print("[salvar] estado gravado")

    a2 = make()
    assert a2.restore() is True
    assert a2.token_focus == ["ETH", "LINK"], a2.token_focus
    assert a2.stop_loss_pct == 3.0
    assert a2.mode == "aggressive"
    assert a2._risk.peak_equity == 150.0
    assert a2._risk.last_trade_ts == 12345.0
    assert len(a2.positions) == 1 and a2.positions[0].symbol == "LINK"
    assert a2.state == AgentState.IN_POSITION
    print(f"[restaurar] foco={a2.token_focus} pico=${a2._risk.peak_equity} "
          f"posicoes={[p.symbol for p in a2.positions]} estado={a2.state.value}  OK")

    print("\n[PASS] Persistencia: estado sobrevive a reinicio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
