"""Boomerang AI — entrypoint. Sobe o agente (Process B) + bot Telegram (Process A).

Uso: .venv\\Scripts\\python run_agent.py
Requer .env preenchido (TWAK, CMC, Claude, Telegram). Configure e ative pelo Telegram.
No Windows, defina TWAK_BIN e NODE_DIR (ver README) se o twak não estiver no PATH.
"""
from __future__ import annotations

import asyncio
import os
import sys

from boomerang.agent import BoomerangAgent
from boomerang.brain.cmc_analyzer import AttentionAnalyzer
from boomerang.config import load_config
from boomerang.interface.telegram_bot import TelegramInterface
from boomerang.ipc import AlertBus
from boomerang.logging_setup import setup_logging
from boomerang.risk import RiskEngine
from boomerang.vault.bnb_validation import BNBValidator
from boomerang.vault.twak_executor import TwakError, TwakExecutor


async def main() -> None:
    cfg = load_config()
    log = setup_logging()
    log.info("Iniciando Boomerang AI...")

    paper = "--paper" in sys.argv
    alerts = AlertBus()
    validator = BNBValidator(cfg, log)
    real_executor = TwakExecutor(cfg, log)
    # Filtro 2 valida pela rota REAL do agregador (cobre V2+V3+outros DEX).
    validator.set_quoter(real_executor)

    if paper:
        from boomerang.vault.paper_executor import PaperExecutor
        log.info("MODO PAPER: execucao simulada (dados CMC reais via x402).")
        executor = PaperExecutor(cfg, validator, starting_cash_usd=100.0,
                                 real_executor=real_executor, logger=log)
    else:
        executor = real_executor

    analyzer = AttentionAnalyzer(cfg, log, executor=executor)  # CMC paga via x402/TWAK

    if not validator.is_connected():
        log.error("Sem conexao com a BSC. Verifique o RPC.")

    try:
        initial_equity = executor.portfolio_usd(cfg.secrets.wallet_password or "")
    except TwakError as exc:
        log.warning("Equity inicial indisponivel (%s). Sera medido no 1o ciclo.", exc)
        initial_equity = 0.0

    risk = RiskEngine(cfg, initial_equity)
    agent = BoomerangAgent(cfg, validator=validator, executor=executor, analyzer=analyzer,
                           risk=risk, alerts=alerts, logger=log)
    iface = TelegramInterface(cfg, agent, alerts, log)

    agent._last_equity = initial_equity
    try:
        agent.agent_address = real_executor.get_address("bsc")
    except Exception as exc:  # noqa: BLE001
        log.warning("Endereco do agente indisponivel: %s", exc)

    # Identidade on-chain ERC-8004 (BNB AI Agent SDK). Só leitura no startup.
    from boomerang.identity import bnb_agent as identity
    from boomerang.ipc import Alert, AlertType
    _id = identity.summary()
    if _id.get("registered"):
        log.info("Identidade ERC-8004: agentId %s em %s (tx %s)",
                 _id["agent_id"], _id["network"], _id["tx"])
        await alerts.emit(Alert(
            AlertType.STARTED, "Identidade on-chain ERC-8004",
            f"agentId {_id['agent_id']} registrado na BNB Chain ({_id['network']}).\n"
            f"Carteira de identidade: {_id['address']}\n"
            f"Prova: {_id['explorer']}",
            {"identity": _id}))
    else:
        log.info("Identidade ERC-8004 ainda nao registrada (rode scripts/register_identity.py).")

    # Equity preciso (on-chain, conta posições abertas) agora que temos o endereço.
    try:
        acc = agent._equity_usd()
        if acc > 0:
            initial_equity = acc
            agent._last_equity = acc
            log.info("Equity inicial (on-chain): $%.2f", acc)
    except Exception as exc:  # noqa: BLE001
        log.warning("Equity on-chain indisponivel no startup: %s", exc)

    # Dashboard web só-leitura (token via /dashboard no Telegram).
    dash_token = os.getenv("DASHBOARD_TOKEN")
    if dash_token:
        from boomerang.webapp.server import serve
        port = int(os.getenv("DASHBOARD_PORT", "8080"))

        def _wallet_provider() -> dict:
            # Lê a composição real da carteira on-chain (BNB + stables + tokens).
            return validator.wallet_breakdown(agent.agent_address or "")

        async def _safe_serve() -> None:
            try:
                await serve(dash_token, port=port, wallet_provider=_wallet_provider)
            except SystemExit:
                log.error("Dashboard nao subiu (porta %d em uso?). Agente segue normal.", port)
            except Exception as exc:  # noqa: BLE001
                log.error("Dashboard falhou: %s", exc)

        asyncio.create_task(_safe_serve())
        log.info("Dashboard ativo em /dash (porta %d).", port)

    # Restaura estado anterior (sobrevive a reinício na semana ao vivo).
    if agent.restore():
        log.info("Estado anterior restaurado.")
    # Anti-contaminação: num restart NÃO-travado o drawdown não pode estar acima do
    # limite de segurança (se estivesse, o disjuntor já teria travado e salvo HALTED).
    # Logo, pico que implique drawdown > limite = estado obsoleto (resíduo de teste/paper).
    safety = float(cfg.hackathon.get("global_drawdown_safety_pct", 23.0))
    peak = agent._risk.peak_equity
    if initial_equity > 0 and peak > 0 and (peak - initial_equity) / peak * 100.0 > safety:
        dd = (peak - initial_equity) / peak * 100.0
        agent._risk.restore_state(initial_equity, agent._risk.last_trade_ts)
        log.info("Pico re-baselinado p/ $%.2f (estado obsoleto: drawdown %.0f%% > limite %.0f%%).",
                 initial_equity, dd, safety)
    agent._save()  # garante estado no disco já no startup (dashboard tem dados na hora)

    await iface.start_polling()

    # Auto-retoma se estava operando antes do restart (resiliência).
    from boomerang.types import AgentState
    if agent.state in (AgentState.SCANNING, AgentState.IN_POSITION):
        log.info("Retomando operacao automaticamente apos restart...")
        await agent.start()

    log.info("Boomerang AI pronto. Configure e ative pelo Telegram (/start).")
    await asyncio.Event().wait()  # roda para sempre


if __name__ == "__main__":
    asyncio.run(main())
