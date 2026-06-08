"""Valida os novos alertas: resumo de ciclo (SCAN) e falha de dados (DATA_ERROR)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _isolate_state  # noqa: F401  isola o estado num tempdir ANTES de importar boomerang

from boomerang.agent import BoomerangAgent
from boomerang.config import load_config
from boomerang.ipc import Alert, AlertBus, AlertType
from boomerang.risk import RiskEngine
from boomerang.types import Action, AgentState, Verdict

ETH = "0x2170Ed0880ac9A755fd29B2688956BD959F933F8"


class FakeExec:
    def portfolio_usd(self, password=None): return 100.0


class FakeVal:
    def onchain_price_usd(self, t): return 100.0
    def validate(self, **k):
        from boomerang.types import ValidationResult
        return ValidationResult(True, k["symbol"], k["token_address"], min_out=1)


class HoldAnalyzer:
    async def gather_global(self): return {}
    async def gather_quotes(self, symbols):
        return {s.upper(): {"volume_change_24h_pct": 40.0, "percent_change_1h": 1.0,
                            "percent_change_24h": 5.0} for s in symbols}
    async def evaluate(self, s, raw_metrics=None): return Verdict(s, 50, Action.HOLD, "fraco")


class NoDataAnalyzer:
    async def gather_global(self): return {}
    async def gather_quotes(self, symbols): return {}
    async def evaluate(self, s, raw_metrics=None): return Verdict(s, 0, Action.HOLD, "x")


def make(analyzer, collector):
    cfg = load_config()
    bus = AlertBus(); bus.subscribe(lambda a: collector.append(a))
    ag = BoomerangAgent(cfg, validator=FakeVal(), executor=FakeExec(), analyzer=analyzer,
                        risk=RiskEngine(cfg, 100.0), alerts=bus)
    ag._token_addr = {"ETH": ETH}; ag.token_focus = ["ETH"]; ag.state = AgentState.SCANNING
    return ag


async def run() -> int:
    # 1) HOLD -> alerta SCAN com resumo
    c1: list[Alert] = []
    await make(HoldAnalyzer(), c1).run_cycle(now=10_000.0)
    scan = [a for a in c1 if a.type == AlertType.SCAN]
    assert scan, f"esperava SCAN, veio {[a.type for a in c1]}"
    print(f"[SCAN] '{scan[0].title}: {scan[0].detail}'  OK")

    # 2) sem dados -> alerta DATA_ERROR
    c2: list[Alert] = []
    await make(NoDataAnalyzer(), c2).run_cycle(now=10_000.0)
    derr = [a for a in c2 if a.type == AlertType.DATA_ERROR]
    assert derr, f"esperava DATA_ERROR, veio {[a.type for a in c2]}"
    print(f"[DATA_ERROR] '{derr[0].title}: {derr[0].detail}'  OK")

    print("\n[PASS] Notificacoes de ciclo e de falha funcionando.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
