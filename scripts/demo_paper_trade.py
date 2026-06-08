"""DEMO sem fundos: ciclo completo com Claude REAL sobre um sinal SIMULADO.

Mostra o sistema inteiro funcionando de graça:
  - Claude (real) avalia metricas mock -> veredito
  - se BUY: PaperExecutor abre posicao (simulada) ao preco controlado
  - movemos o preco e o monitor fecha no stop-loss
Unico "falso": o sinal de mercado (mock) e o preco scriptado. Claude e a logica
sao reais. Roda com: .venv\\Scripts\\python scripts\\demo_paper_trade.py
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
from boomerang.vault.paper_executor import PaperExecutor

ETH = "0x2170Ed0880ac9A755fd29B2688956BD959F933F8"

# Sinal SIMULADO bullish-porem-saudavel (formato REST, passa no pre-filtro).
MOCK_SIGNAL = {
    "price_usd": 100.0,
    "volume_24h_usd": 1.3e10,
    "volume_change_24h_pct": 42.0,
    "percent_change_1h": 1.2,
    "percent_change_24h": 3.1,
    "percent_change_7d": 6.0,
    "market_cap_usd": 2.0e11,
}


class ScriptedValidator:
    """Preco controlado + validacao aprovada (a validacao real ja foi provada)."""
    def __init__(self, price): self.price = price
    def validate(self, *, symbol, token_address, amount_usd, cmc_price_usd=None):
        from boomerang.types import ValidationResult
        return ValidationResult(True, symbol, token_address, estimated_slippage_pct=0.1,
                                expected_out=1000, min_out=995, detail="ok (demo)")
    def onchain_price_usd(self, token_address): return self.price


async def printer(a: Alert) -> None:
    extra = ""
    if a.data.get("entry"): extra = f" | entrada ${a.data['entry']:.2f}"
    if a.data.get("pnl_pct") is not None: extra = f" | PnL {a.data['pnl_pct']:+.2f}%"
    print(f"  [BOT->Telegram] {a.title}: {a.detail}{extra}")


async def main() -> int:
    cfg = load_config()
    log = setup_logging()
    val = ScriptedValidator(price=100.0)
    paper = PaperExecutor(cfg, val, starting_cash_usd=100.0, logger=log)
    analyzer = AttentionAnalyzer(cfg, log)

    # injeta o sinal simulado no lugar da coleta da CMC (global + batch de cotações)
    async def fake_global(): return {}
    async def fake_quotes(symbols): return {s.upper(): MOCK_SIGNAL for s in symbols}
    analyzer.gather_global = fake_global
    analyzer.gather_quotes = fake_quotes

    risk = RiskEngine(cfg, 100.0)
    alerts = AlertBus(); alerts.subscribe(printer)
    agent = BoomerangAgent(cfg, validator=val, executor=paper, analyzer=analyzer,
                           risk=risk, alerts=alerts, logger=log)
    agent._token_addr = {"ETH": ETH}
    agent.configure(token_focus=["ETH"], stop_loss_pct=5.0, mode="aggressive")  # min score 80
    agent.state = AgentState.SCANNING

    print(">> Claude avaliando o sinal simulado de ETH (real)...")
    v = await analyzer.evaluate("ETH", raw_metrics=MOCK_SIGNAL)
    print(f">> Veredito do Claude: score={v.confidence_score} action={v.action.value}")
    print(f"   racional: {v.rationale}\n")

    print(">> Rodando ciclo de scan (paper, preco $100)...")
    await agent.run_cycle(time.time())
    if not agent.positions:
        print(">> Claude optou por nao operar (HOLD). Sistema funcionando — apenas cauteloso.")
        return 0

    print(f">> Posicao aberta. Portfolio: ${paper.portfolio_usd():.2f}")
    print("\n>> Simulando queda de preco para -6% (gatilho de stop-loss)...")
    val.price = 94.0
    await agent.check_positions()
    print(f">> Portfolio final: ${paper.portfolio_usd():.2f} | posicoes: {[p.symbol for p in agent.positions]}")

    print("\n[DEMO OK] Ciclo completo: Claude decidiu, abriu (paper) e fechou no stop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
