"""Demonstra o comando /buy <SYM> pelo CAMINHO REAL do agente (force_buy).

Mesma função que o botão/comando do Telegram dispara: risco -> Filtro 2 -> TWAK.
Deixa a posição ABERTA (aparece no painel e passa a ser monitorada após reinício).

Uso: .venv\\Scripts\\python scripts\\demo_buy.py [SYMBOL]
Gasta dinheiro REAL (abre uma posição ~$2). Requer .env.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv

from boomerang.agent import BoomerangAgent
from boomerang.brain.cmc_analyzer import AttentionAnalyzer
from boomerang.config import load_config
from boomerang.ipc import AlertBus
from boomerang.logging_setup import setup_logging
from boomerang.risk import RiskEngine
from boomerang.vault.bnb_validation import BNBValidator
from boomerang.vault.twak_executor import TwakExecutor

load_dotenv()


async def main() -> int:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "ADA").upper()
    cfg = load_config()
    log = setup_logging()

    alerts = AlertBus()

    def printer(a):  # noqa: ANN001
        print(f"   [ALERTA] {a.type.value}: {a.title}" + (f" — {a.detail}" if a.detail else ""))
    alerts.subscribe(printer)

    validator = BNBValidator(cfg, log)
    executor = TwakExecutor(cfg, log)
    validator.set_quoter(executor)  # Filtro 2 via agregador (V2+V3)
    analyzer = AttentionAnalyzer(cfg, log, executor=executor)
    equity = executor.portfolio_usd(cfg.secrets.wallet_password or "")
    risk = RiskEngine(cfg, equity)
    agent = BoomerangAgent(cfg, validator=validator, executor=executor, analyzer=analyzer,
                           risk=risk, alerts=alerts, logger=log)
    agent._last_equity = equity
    agent.agent_address = executor.get_address("bsc")

    print(f"\nEquity atual: ${equity:.2f}")
    print(f">>> Simulando exatamente o comando do Telegram:  /buy {symbol}\n")

    await agent.force_buy(symbol)

    print(f"\nEstado do agente: {agent.state.value}")
    if agent.positions:
        for p in agent.positions:
            print(f"POSICAO ABERTA: {p.symbol} | ${p.amount_usd:.2f} | entrada {p.entry_price:.6g} "
                  f"| stop {p.stop_loss_price:.6g}")
            print(f"   tx compra: https://bscscan.com/tx/{p.tx_hash}")
        print("\n[OK] /buy funcionou: posicao real aberta e salva (vai aparecer no painel).")
    else:
        print("\n[!] Nenhuma posicao aberta — veja o alerta acima para o motivo.")
    agent._save()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
