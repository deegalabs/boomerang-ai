"""Boomerang AI — entrypoint. Brings up the agent (Process B) + Telegram bot (Process A).

Usage: .venv\\Scripts\\python run_agent.py
Requires a filled-in .env (TWAK, CMC, Claude, Telegram). Configure and activate via Telegram.
On Windows, set TWAK_BIN and NODE_DIR (see README) if twak is not on the PATH.
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
    log.info("Starting Boomerang AI...")

    paper = "--paper" in sys.argv
    alerts = AlertBus()
    validator = BNBValidator(cfg, log)
    real_executor = TwakExecutor(cfg, log)
    # Filter 2 validates via the aggregator's REAL route (covers V2+V3+other DEXes).
    validator.set_quoter(real_executor)

    if paper:
        from boomerang.vault.paper_executor import PaperExecutor
        log.info("PAPER MODE: simulated execution (real CMC data via x402).")
        executor = PaperExecutor(cfg, validator, starting_cash_usd=100.0,
                                 real_executor=real_executor, logger=log)
    else:
        executor = real_executor

    analyzer = AttentionAnalyzer(cfg, log, executor=executor)  # CMC paid via x402/TWAK

    if not validator.is_connected():
        log.error("No connection to BSC. Check the RPC.")

    try:
        initial_equity = executor.portfolio_usd(cfg.secrets.wallet_password or "")
    except TwakError as exc:
        log.warning("Initial equity unavailable (%s). Will be measured on the 1st cycle.", exc)
        initial_equity = 0.0

    risk = RiskEngine(cfg, initial_equity)
    agent = BoomerangAgent(cfg, validator=validator, executor=executor, analyzer=analyzer,
                           risk=risk, alerts=alerts, logger=log)
    iface = TelegramInterface(cfg, agent, alerts, log)

    agent._last_equity = initial_equity
    try:
        agent.agent_address = real_executor.get_address("bsc")
    except Exception as exc:  # noqa: BLE001
        log.warning("Agent address unavailable: %s", exc)

    # On-chain ERC-8004 identity (BNB AI Agent SDK). Read-only at startup.
    from boomerang.identity import bnb_agent as identity
    from boomerang.ipc import Alert, AlertType
    _id = identity.summary()
    if _id.get("registered"):
        log.info("ERC-8004 identity: agentId %s on %s (tx %s)",
                 _id["agent_id"], _id["network"], _id["tx"])
        await alerts.emit(Alert(
            AlertType.STARTED, "On-chain ERC-8004 identity",
            f"agentId {_id['agent_id']} registered on BNB Chain ({_id['network']}).\n"
            f"Identity wallet: {_id['address']}\n"
            f"Proof: {_id['explorer']}",
            {"identity": _id}))
    else:
        log.info("ERC-8004 identity not yet registered (run scripts/register_identity.py).")

    # Precise equity (on-chain, counts open positions) now that we have the address.
    try:
        acc = agent._equity_usd()
        if acc > 0:
            initial_equity = acc
            agent._last_equity = acc
            log.info("Initial equity (on-chain): $%.2f", acc)
    except Exception as exc:  # noqa: BLE001
        log.warning("On-chain equity unavailable at startup: %s", exc)

    # Read-only web dashboard (token via /dashboard on Telegram).
    dash_token = os.getenv("DASHBOARD_TOKEN")
    if dash_token:
        from boomerang.webapp.server import serve
        port = int(os.getenv("DASHBOARD_PORT", "8080"))

        def _wallet_provider() -> dict:
            # Reads the real on-chain wallet composition (BNB + stables + tokens).
            return validator.wallet_breakdown(agent.agent_address or "")

        async def _safe_serve() -> None:
            try:
                await serve(dash_token, port=port, wallet_provider=_wallet_provider)
            except SystemExit:
                log.error("Dashboard did not start (port %d in use?). Agent runs normally.", port)
            except Exception as exc:  # noqa: BLE001
                log.error("Dashboard failed: %s", exc)

        asyncio.create_task(_safe_serve())
        log.info("Dashboard active at /dash (port %d).", port)

    # Restore previous state (survives a restart during the live week).
    if agent.restore():
        log.info("Previous state restored.")
    # Anti-contamination: on a NON-halted restart the drawdown cannot be above the
    # safety limit (if it were, the circuit breaker would already have halted and saved HALTED).
    # So a peak implying drawdown > limit = stale state (test/paper residue).
    safety = float(cfg.hackathon.get("global_drawdown_safety_pct", 23.0))
    peak = agent._risk.peak_equity
    if initial_equity > 0 and peak > 0 and (peak - initial_equity) / peak * 100.0 > safety:
        dd = (peak - initial_equity) / peak * 100.0
        agent._risk.restore_state(initial_equity, agent._risk.last_trade_ts)
        log.info("Peak re-baselined to $%.2f (stale state: drawdown %.0f%% > limit %.0f%%).",
                 initial_equity, dd, safety)
    agent._save()  # ensures state on disk right at startup (dashboard has data immediately)

    await iface.start_polling()

    # Liveness signal: beats every 30s REGARDLESS of trade state. If the event loop
    # freezes/dies, it stops beating → /healthz returns 503 → Railway restarts the container.
    from boomerang import liveness

    async def _liveness_loop() -> None:
        while True:
            liveness.beat()
            await asyncio.sleep(30)

    asyncio.create_task(_liveness_loop())
    liveness.beat()  # 1st beat right at startup

    # If this is a RESTART after a crash (railway_start supervisor), notify the owner.
    restarts = int(os.getenv("BOOMERANG_RESTART_COUNT", "0") or "0")
    if restarts > 0:
        await alerts.emit(Alert(AlertType.ERROR, "♻️ Agent restarted",
                                f"The agent crashed and recovered on its own (restart #{restarts}). "
                                "Operation resumed; state restored from disk."))

    # Auto-resumes if it was trading before the restart (resilience).
    from boomerang.types import AgentState
    if agent.state in (AgentState.SCANNING, AgentState.IN_POSITION):
        log.info("Automatically resuming operation after restart...")
        await agent.start()

    log.info("Boomerang AI ready. Configure and activate via Telegram (/start).")
    await asyncio.Event().wait()  # runs forever


if __name__ == "__main__":
    asyncio.run(main())
