"""Teste de orquestracao ponta a ponta com stubs deterministicos.

Valida: ciclo de scan abre posicao (F1->F2->F3), monitor fecha no stop-loss,
e panico liquida + trava. Sem credenciais/rede.
Roda com: .venv\\Scripts\\python scripts\\test_orchestration.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _isolate_state  # noqa: F401  isola o estado num tempdir ANTES de importar boomerang

from boomerang.agent import BoomerangAgent
from boomerang.config import load_config
from boomerang.ipc import Alert, AlertBus, AlertType
from boomerang.risk import RiskEngine
from boomerang.types import Action, AgentState, ExecutionResult, ValidationResult, Verdict

ETH = "0x2170Ed0880ac9A755fd29B2688956BD959F933F8"  # so para o mapa de teste


class FakeExecutor:
    def __init__(self):
        self.equity = 100.0
    def portfolio_usd(self, password):
        return self.equity
    def buy(self, *, to_token, amount_usd, password, slippage_pct=None):
        return ExecutionResult(True, to_token, tx_hash="0xbuy", entry_price=100.0, qty=0.05)
    def sell_all(self, *, token, amount, password, slippage_pct=None):
        return ExecutionResult(True, token, tx_hash="0xsell")
    def transfer_to_owner(self, *, to, amount, token, password, max_usd=None):
        return {"txHash": "0xwithdraw"}


class FakeValidator:
    def __init__(self):
        self.price = 94.0  # abaixo do stop (95) p/ disparar venda no monitor
    def validate(self, *, symbol, token_address, amount_usd, cmc_price_usd=None):
        return ValidationResult(True, symbol, token_address, estimated_slippage_pct=0.1,
                                expected_out=1000, min_out=995, detail="ok")
    def onchain_price_usd(self, token_address):
        return self.price


class FakeAnalyzer:
    async def gather_global(self): return {"btc_dominance_pct": 58}
    async def gather_quotes(self, symbols):
        return {s.upper(): {"volume_change_24h_pct": 40.0, "percent_change_1h": 1.0,
                            "percent_change_24h": 5.0, "volume_24h_usd": 1e9} for s in symbols}
    async def evaluate(self, symbol, raw_metrics=None):
        return Verdict(symbol, 95, Action.BUY, "momentum saudavel")


def build_agent(collector):
    cfg = load_config()
    alerts = AlertBus()
    alerts.subscribe(lambda a: collector.append(a))
    risk = RiskEngine(cfg, initial_equity_usd=100.0)
    agent = BoomerangAgent(cfg, validator=FakeValidator(), executor=FakeExecutor(),
                           analyzer=FakeAnalyzer(), risk=risk, alerts=alerts)
    agent._token_addr = {"ETH": ETH}
    agent.token_focus = ["ETH"]
    agent.stop_loss_pct = 5.0
    return agent


async def run() -> int:
    collector: list[Alert] = []
    agent = build_agent(collector)
    agent.state = AgentState.SCANNING

    # 1) ciclo de scan abre posicao
    await agent.run_cycle(now=10_000.0)
    assert len(agent.positions) == 1, "deveria ter aberto 1 posicao"
    assert any(a.type == AlertType.TRADE_OPENED for a in collector), "faltou alerta TRADE_OPENED"
    print(f"[scan] abriu posicao {agent.positions[0].symbol} @ {agent.positions[0].entry_price} "
          f"(stop {agent.positions[0].stop_loss_price})  OK")

    # 2) monitor fecha no stop-loss (preco 94 < 95)
    await agent.check_positions()
    assert len(agent.positions) == 0, "deveria ter fechado no stop"
    closed = [a for a in collector if a.type == AlertType.TRADE_CLOSED]
    assert closed, "faltou alerta TRADE_CLOSED"
    print(f"[monitor] fechou no stop-loss ({closed[-1].detail})  OK")

    # 3) panico liquida tudo e trava
    agent2 = build_agent(collector)
    agent2.state = AgentState.SCANNING
    await agent2.run_cycle(now=20_000.0)
    assert len(agent2.positions) == 1
    await agent2.panic("teste manual")
    assert agent2.state == AgentState.HALTED and len(agent2.positions) == 0
    assert any(a.type == AlertType.CIRCUIT_BREAKER for a in collector)
    print("[panic] liquidou tudo e travou o agente (HALTED)  OK")

    print("\n[PASS] Orquestracao ponta a ponta validada (F1->F2->F3 + risco).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
