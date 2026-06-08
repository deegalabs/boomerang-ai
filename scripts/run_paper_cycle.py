"""Roda 1 ciclo do agente em MODO PAPER (execucao simulada), sem Telegram.

Dados CMC reais (via x402, exige USDC na Base) + Claude real + validacao BSC real
+ execucao SIMULADA. Sem Base fundeada, a coleta CMC falha (402) e o agente fica
em HOLD — o que ainda valida o loop completo rodando sem travar.

Uso: .venv\\Scripts\\python scripts\\run_paper_cycle.py [SYMBOLO]
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.agent import BoomerangAgent
from boomerang.brain.cmc_analyzer import AttentionAnalyzer
from boomerang.config import load_config
from boomerang.ipc import Alert, AlertBus
from boomerang.logging_setup import setup_logging
from boomerang.risk import RiskEngine
from boomerang.types import AgentState
from boomerang.vault.bnb_validation import BNBValidator
from boomerang.vault.paper_executor import PaperExecutor
from boomerang.vault.twak_executor import TwakExecutor


async def printer(a: Alert) -> None:
    print(f"  [ALERT] {a.type.value}: {a.title} — {a.detail}")


async def main() -> int:
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "ETH"
    cfg = load_config()
    log = setup_logging()

    validator = BNBValidator(cfg, log)
    real = TwakExecutor(cfg, log)
    paper = PaperExecutor(cfg, validator, starting_cash_usd=100.0, real_executor=real, logger=log)
    analyzer = AttentionAnalyzer(cfg, log, executor=paper)
    risk = RiskEngine(cfg, 100.0)
    alerts = AlertBus(); alerts.subscribe(printer)
    agent = BoomerangAgent(cfg, validator=validator, executor=paper, analyzer=analyzer,
                           risk=risk, alerts=alerts, logger=log)
    agent.token_focus = [symbol]
    agent.state = AgentState.SCANNING

    print(f"RPC conectado: {validator.is_connected()} | foco: {symbol} | banca paper: $100")
    print("Rodando 1 ciclo de scan (paper)...\n")
    await agent.run_cycle(time.time())
    await agent.check_positions()

    print("\nStatus final:")
    st = await agent.status()
    for k, v in st.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
