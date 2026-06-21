"""Boomerang AI orchestrator — the loop that unites the three filters + risk engine.

Process B (agent logic). Does NOT know about Telegram; only emits alerts via the
AlertBus. The interface sends control intents (configure/pause/panic/withdraw).

Scan loop (config interval) → Filter 1 (CMC) → Filter 2 (BNB) → Filter 3
(TWAK). Monitor loop (2s) → stop-loss / trailing per position.
All synchronous external calls run in a thread so they don't block the loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path

from boomerang.brain.cmc_analyzer import AttentionAnalyzer, momentum_prescore
from boomerang.risk.risk_engine import (
    conviction_size_pct, equity_reading_reliable, market_regime, overextension_factor)
from boomerang import market_cache
from boomerang.config import Config
from boomerang.identity import bnb_agent as identity
from boomerang.ipc import Alert, AlertBus, AlertType
from boomerang.persistence import (
    append_trade, load_state, load_trades, save_state, verify_state_integrity)
from boomerang.risk import RiskEngine
from boomerang.risk.audit import audit
from boomerang.risk.risk_engine import ExitSignal
from boomerang.strategy.confluence import evaluate_confluence
from boomerang.strategy.indicators import compute_indicators
from boomerang.strategy.klines import fetch_klines
from boomerang.strategy.playbook import (
    expectancy_disabled, regime_posture, select_strategy, setup_strength, ta_select)
from boomerang.strategy.projection import project
from boomerang.types import AgentState, Position
from boomerang.vault.bnb_validation import BNBValidator
from boomerang.vault.twak_executor import TwakError, TwakExecutor

ROOT = Path(__file__).resolve().parent.parent


class BoomerangAgent:
    def __init__(
        self,
        config: Config,
        *,
        validator: BNBValidator,
        executor: TwakExecutor,
        analyzer: AttentionAnalyzer,
        risk: RiskEngine,
        alerts: AlertBus,
        logger: logging.Logger | None = None,
    ) -> None:
        self._cfg = config
        self._log = logger or logging.getLogger("boomerang.agent")
        self._validator = validator
        self._executor = executor
        self._analyzer = analyzer
        self._risk = risk
        self._alerts = alerts

        self.state = AgentState.IDLE
        self.positions: list[Position] = []
        self._password = config.secrets.wallet_password or ""
        self._owner = config.secrets.owner_wallet_address or ""
        self._token_addr = self._load_token_map()

        # user layer (adjustable via Telegram)
        self.token_focus: list[str] = list(config.user.get("token_focus", []))
        # original curated basket (preserved before restore(), so the "recommended" button
        # can restore breadth if the focus was reduced to 1 coin)
        self._default_focus: list[str] = list(config.user.get("token_focus", []))
        self._default_size: float = config.user_position_size_pct  # default size for reset
        self.stop_loss_pct: float = config.user_stop_loss_pct
        self.take_profit_pct: float = config.user_take_profit_pct
        self.position_size_pct: float = config.user_position_size_pct
        self.mode: str = config.user.get("mode", "conservative")
        self._tasks: list[asyncio.Task] = []
        # tokens whose EXECUTION failed (e.g.: revert due to liquidity): symbol -> expiry ts.
        # The agent ignores them for a while and routes to tradable candidates (self-learns).
        self._exec_cooldown: dict[str, float] = {}
        # Smart Exit skill: last brain re-evaluation per position (throttle).
        self._exit_checks: dict[str, float] = {}
        # On-chain Reputation skill: timestamp of last publication (throttle ~1x/hour).
        self._last_rep_publish: float = 0.0
        self._last_risk_publish: float = 0.0
        self._last_depeg_alert: float = 0.0
        self._last_x402: float = 0.0          # throttle for the in-loop x402 derivatives payment (~1x/hour)
        self._x402_deriv: dict = {}           # latest CMC derivatives fetched via x402 (fed to the brain)
        self._extreme_greed: bool = False     # F&G >= 78 → tightens exits (locks profit at the top)
        self._fng_ema: float | None = None    # smoothed F&G average to read the DIRECTION (fear easing vs cooling)
        self._last_equity: float = 0.0          # most recent equity (cache for dashboard)
        self._last_holdings: list = []          # composition per coin (cache for /live panel)
        self._last_mon_save: float = 0.0        # throttle for monitor-driven state saves (live PnL)
        self._last_traces: list = []            # decision trace: why each candidate did NOT enter
        self._last_posture: str = ""            # latest Action-Matrix posture label (for /status parity)
        self._last_seal: dict | None = None     # last reasoning sealed on-chain (for the /live "verify" panel)
        self.agent_address: str | None = None   # wallet address (filled in at startup)

    # ── loading of eligible addresses ────────────────────────────────────────
    def _load_token_map(self) -> dict[str, str]:
        path = ROOT / self._cfg.hackathon["eligible_tokens_file"]
        # Excludes the base stables (USDC/USDT): they are NOT trade targets, and counting them
        # as a "token" would double the balance in the composition (they already enter as 'stable').
        bases = {"USDC", "USDT"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {sym.upper(): addr for sym, addr in data.get("tokens", {}).items()
                    if sym.upper() not in bases}
        except FileNotFoundError:
            return {}

    def _addr(self, symbol: str) -> str | None:
        return self._token_addr.get(symbol.upper())

    async def _emit(self, type_: AlertType, title: str, detail: str = "", **data) -> None:
        # Visibility in the server log for events that explain why it did NOT trade
        # or that require attention (without this the reason only went to Telegram — blind spot).
        if type_ in (AlertType.REJECTED, AlertType.ERROR, AlertType.DATA_ERROR,
                     AlertType.CIRCUIT_BREAKER):
            self._log.info("%s | %s — %s", type_.name, title, detail)
            audit(type_.name.lower(), title=title, detail=detail)  # forensic trail
        await self._alerts.emit(Alert(type_, title, detail, data))

    # ── equity measurement ───────────────────────────────────────────────────
    def _equity_usd_checked(self) -> tuple[float, bool]:
        """Total equity in USD read ON-CHAIN + RELIABILITY flag.

        The reading is UNreliable when an open position could not be priced
        (limited RPC / no price route) — then the equity comes deflated and must NOT
        trip the drawdown circuit breaker. Falls back to the TWAK portfolio (its own
        price source, treated as reliable) only if the on-chain reading fails entirely.
        """
        addr = self.agent_address
        if addr:
            try:
                bd = self._validator.wallet_breakdown(addr)
                total = float(bd.get("total_usd") or 0.0)
                if total > 0:
                    holdings = bd.get("holdings", [])
                    self._last_holdings = holdings
                    reliable = equity_reading_reliable(holdings, [p.symbol for p in self.positions])
                    return total, reliable
            except Exception as exc:  # noqa: BLE001
                self._log.warning("On-chain equity unavailable (%s); using TWAK.", exc)
        return self._executor.portfolio_usd(self._password), True

    def _equity_usd(self) -> float:
        """Total equity in USD (without the flag — for status/withdraw/heartbeat)."""
        return self._equity_usd_checked()[0]

    def _stable_usd(self) -> float:
        """Balance of the trade stable (base_stable, e.g.: USDC) in USD — how much the
        agent can ACTUALLY spend on a buy. Different from total equity (which
        includes open positions and gas). Read from the most recent on-chain breakdown
        (_last_holdings, updated by _equity_usd). Avoids trying to buy more
        stable than exists (cause of a swap reverted by insufficient balance)."""
        base = str(self._cfg.dev_safety.get("base_stable_symbol", "USDC")).upper()
        for h in self._last_holdings:
            if str(h.get("symbol", "")).upper() == base:
                return float(h.get("value_usd") or 0.0)
        return 0.0

    def _stable_depeg_bps(self, macro: dict) -> float | None:
        """Deviation (in bps) of the TRADE stable from $1, from the CMC reference.
        None if the price did not come. base USDC → usdc_price; base USDT → usdt_price."""
        base = str(self._cfg.dev_safety.get("base_stable_symbol", "USDC")).upper()
        price = macro.get("usdc_price") if base == "USDC" else macro.get("usdt_price")
        if not price or price <= 0:
            return None
        return abs(price - 1.0) * 10000.0

    async def _maybe_alert_depeg(self, depeg_bps: float) -> None:
        """Depeg alert (throttled ~1x/30min) — doesn't spam every cycle."""
        now = time.time()
        if now - self._last_depeg_alert < 1800:
            return
        self._last_depeg_alert = now
        base = str(self._cfg.dev_safety.get("base_stable_symbol", "USDC")).upper()
        await self._emit(AlertType.CIRCUIT_BREAKER, "🛡️ Depeg detected",
                         f"{base} deviated {depeg_bps/100:.2f}% from $1 — entries blocked "
                         f"until it re-anchors. Capital protected, no new buys.")

    async def _reconcile_positions(self) -> None:
        """Syncs the tracking with the REAL on-chain wallet, in both directions:
        (1) discards tracked positions that no longer exist on-chain (external sells
            / dust), comparing the REAL token BALANCE (not the USD value, subject to
            junk pricing from thin liquidity);
        (2) IMPORTS holdings of eligible tokens that are in the wallet but are NOT
            tracked (leftovers from testing/restart) — otherwise they're left without stop-loss/management."""
        if not self.agent_address:
            return
        if not self.positions:
            await self._import_orphans()
            return

        def _onchain_qty(pos: Position) -> float:
            raw = self._validator._token_balance(pos.token_address, self.agent_address)
            return raw / (10 ** self._validator._decimals(pos.token_address))

        kept, dropped = [], []
        for pos in self.positions:
            try:
                onchain = await asyncio.to_thread(_onchain_qty, pos)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("Reconciliation: did not check %s (%s); keeping.", pos.symbol, exc)
                kept.append(pos)
                continue
            # Dust (negligible on-chain value, sell leftover) → unlocks the tracking.
            try:
                price = await asyncio.to_thread(self._validator.onchain_price_usd, pos.token_address)
                if onchain * price < 0.20:
                    dropped.append(pos.symbol)
                    continue
            except Exception:  # noqa: BLE001
                pass
            if pos.qty > 0 and onchain >= pos.qty * 0.20:  # most of the position still remains
                kept.append(pos)
            else:
                dropped.append(pos.symbol)
        if dropped:
            self.positions = kept
            self._save()
            self._log.info("Reconciliation: discarded %d position(s) without on-chain balance: %s",
                           len(dropped), ", ".join(dropped))
        await self._import_orphans()

    async def _import_orphans(self) -> None:
        """Imports on-chain holdings of eligible tokens that are NOT tracked (leftovers from
        testing/restart), taking over their management (stop-loss/trailing). Entry = current
        price: protection takes effect from now on."""
        if not self.agent_address:
            return
        try:
            bd = await asyncio.to_thread(self._validator.wallet_breakdown, self.agent_address)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Orphan import: breakdown unavailable (%s).", exc)
            return
        tracked = {p.symbol.upper() for p in self.positions}
        now = time.time()
        imported: list[str] = []
        for h in bd.get("holdings", []):
            sym = str(h.get("symbol", "")).upper()
            if h.get("kind") != "token" or sym in tracked:
                continue
            addr = self._addr(sym)
            entry = float(h.get("price_usd") or 0.0)
            qty = float(h.get("balance") or 0.0)
            if not addr or entry <= 0 or qty <= 0 or float(h.get("value_usd") or 0.0) < self._cfg.min_position_usd:
                continue
            self.positions.append(Position(
                symbol=sym, token_address=addr, entry_price=entry,
                amount_usd=float(h["value_usd"]), qty=qty,
                stop_loss_price=self._risk.initial_stop_price(entry),
                opened_at=now, tx_hash="importado"))
            imported.append(sym)
        if imported:
            if self.state == AgentState.SCANNING:
                self.state = AgentState.IN_POSITION
            self._save()
            self._log.info("Imported %d orphan holding(s) for management: %s", len(imported), ", ".join(imported))
            await self._emit(AlertType.STARTED, "📥 Positions imported for management",
                             f"Now monitoring (stop-loss): {', '.join(imported)}")

    # ── control (called by the interface) ────────────────────────────────────
    def configure(self, *, token_focus: list[str] | None = None,
                  stop_loss_pct: float | None = None, take_profit_pct: float | None = None,
                  position_size_pct: float | None = None, mode: str | None = None,
                  target_return_pct: float | None = None, enable_ev_filter: bool | None = None) -> None:
        # Propagates to cfg.user (single source read by the analyzer and the risk engine).
        if target_return_pct is not None:
            self._cfg.dev_safety["target_return_pct"] = max(0.0, float(target_return_pct))
        if enable_ev_filter is not None:
            self._cfg.dev_safety["enable_ev_filter"] = bool(enable_ev_filter)
        if token_focus is not None:
            self.token_focus = [t.upper() for t in token_focus]
            self._cfg.user["token_focus"] = self.token_focus
        if stop_loss_pct is not None:
            self.stop_loss_pct = float(stop_loss_pct)
            self._cfg.user["stop_loss_pct"] = self.stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = float(take_profit_pct)
            self._cfg.user["take_profit_pct"] = self.take_profit_pct
        if position_size_pct is not None:
            self.position_size_pct = float(position_size_pct)
            self._cfg.user["position_size_pct"] = self.position_size_pct
        if mode is not None:
            self.mode = mode
            self._cfg.user["mode"] = mode
        self._save()

    # ── persistence (survives restart) ───────────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "token_focus": self.token_focus,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "target_return_pct": self._cfg.dev_safety.get("target_return_pct", 0.0),
            "position_size_pct": self.position_size_pct,
            "mode": self.mode,
            "peak_equity": self._risk.peak_equity,
            "equity_usd": self._last_equity,
            "drawdown_pct": (self._risk.current_drawdown_pct(self._last_equity)
                             if self._last_equity else 0.0),
            "daily_drawdown_pct": (self._risk.daily_drawdown_pct(self._last_equity)
                                   if self._last_equity else 0.0),
            "breaker_pct": self._cfg.drawdown_safety_pct,
            "daily_cap_pct": self._cfg.daily_loss_cap_pct,
            "halted": self._risk.halted,
            "posture": self._last_posture or None,
            "last_trade_ts": self._risk.last_trade_ts,
            "agent_address": self.agent_address,
            "holdings": self._last_holdings,
            "positions": [asdict(p) for p in self.positions],
            "identity": identity.summary(),
            "traces": self._last_traces[-12:],
            "last_seal": self._last_seal,
            "llm": self._llm_usage(),
        }

    def _save(self) -> None:
        try:
            save_state(self.snapshot())
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Failed to save state: %s", exc)

    def _llm_usage(self):
        """LLM token-usage summary for /live — defensive so a brain hiccup never blocks _save."""
        try:
            return self._analyzer.usage_summary()
        except Exception:  # noqa: BLE001
            return None

    def restore(self) -> bool:
        """Loads saved state (if any). Returns True if it restored."""
        if not verify_state_integrity():
            # State file tampered (HMAC mismatch) → refuse to trade on it, freeze + audit.
            self._risk.halt()
            self.state = AgentState.HALTED
            audit("state_integrity_fail", action="halt")
            self._log.critical("STATE INTEGRITY FAIL — HMAC mismatch. Halted; manual review required.")
        data = load_state()
        if not data:
            return False
        try:
            self.state = AgentState(data.get("state", self.state.value))
        except ValueError:
            pass
        self.token_focus = data.get("token_focus", self.token_focus)
        # Surgical reset (BOOMERANG_RESET_FOCUS=1): ignores focus/size saved from tests
        # and restores the config defaults (liquid basket + 25%) — without touching stop/profit.
        # Unlocks the autonomous agent without reconfiguring everything in the bot.
        if os.getenv("BOOMERANG_RESET_FOCUS", "").strip() in ("1", "true", "True") and self._default_focus:
            self.token_focus = list(self._default_focus)
            self.position_size_pct = self._default_size  # also reverts to the default size (25%)
            data["position_size_pct"] = self._default_size  # don't let restore overwrite below
            data["peak_equity"] = 0.0  # rebases the peak (clears phantom peak) → drawdown 0% on the 1st cycle
            self._log.info("Reset: focus→basket (%d coins), size→%.0f%%, peak rebased.",
                           len(self.token_focus), self.position_size_pct)
        self.stop_loss_pct = data.get("stop_loss_pct", self.stop_loss_pct)
        self.take_profit_pct = data.get("take_profit_pct", self.take_profit_pct)
        self.position_size_pct = data.get("position_size_pct", self.position_size_pct)
        self.mode = data.get("mode", self.mode)
        self._cfg.user["token_focus"] = self.token_focus
        self._cfg.user["stop_loss_pct"] = self.stop_loss_pct
        self._cfg.user["take_profit_pct"] = self.take_profit_pct
        self._cfg.user["position_size_pct"] = self.position_size_pct
        self._cfg.user["mode"] = self.mode
        self._risk.restore_state(data.get("peak_equity", 0.0), data.get("last_trade_ts", 0.0))
        self.positions = [Position(**p) for p in data.get("positions", [])]
        self._log.info("State restored: %d position(s), peak $%.2f",
                       len(self.positions), self._risk.peak_equity)
        return True

    def _start_loops(self) -> None:
        self._cancel_loops()
        self._tasks = [
            asyncio.create_task(self._scan_loop()),
            asyncio.create_task(self._monitor_loop()),
        ]

    def _cancel_loops(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []

    async def start(self) -> None:
        if self.state == AgentState.HALTED:
            await self._emit(AlertType.ERROR, "Agent halted",
                             "Circuit breaker active (panic/withdraw). Use */reiniciar* to unlock and resume trading.")
            return
        # Syncs with the wallet: discards already-sold/dust positions before trading.
        await self._reconcile_positions()
        # Keeps IN_POSITION if there's already an open position (e.g.: resume after restart).
        self.state = AgentState.IN_POSITION if self.positions else AgentState.SCANNING
        await self._emit(AlertType.STARTED, "Boomerang AI active",
                         f"Focus: {', '.join(self.token_focus)} | size {self.position_size_pct:.0f}% | "
                         f"stop {self.stop_loss_pct}% | mode {self.mode}")
        self._start_loops()

    async def pause(self) -> None:
        self.state = AgentState.PAUSED
        self._cancel_loops()  # stops NOW (doesn't wait for the cycle to finish/sleep)
        await self._emit(AlertType.PAUSED, "Agent paused", "Scanning interrupted. Use /start to resume.")

    async def resume(self) -> None:
        if self.state == AgentState.PAUSED:
            self.state = AgentState.SCANNING
            await self._emit(AlertType.STARTED, "Agent resumed")
            self._start_loops()

    async def restart_session(self) -> None:
        """Unlocks after HALTED (panic/withdraw/circuit breaker) and resumes trading. Clears the circuit
        breaker, rebases the peak to current equity (fresh start) and reconciles the wallet.
        Deliberate use by the owner via /reiniciar."""
        self._risk.clear_halt()
        try:
            eq = await asyncio.to_thread(self._equity_usd)
            if eq > 0:
                self._risk.restore_state(eq, self._risk.last_trade_ts)  # rebases the peak
                self._last_equity = eq
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Restart: equity unavailable (%s).", exc)
        await self._reconcile_positions()
        self.state = AgentState.IN_POSITION if self.positions else AgentState.SCANNING
        self._save()
        await self._emit(AlertType.STARTED, "🔄 Session restarted",
                         "Halt cleared, peak rebased. Trading again.")
        self._start_loops()

    async def stop(self) -> None:
        self._cancel_loops()

    # ── circuit breaker / panic ──────────────────────────────────────────────
    async def panic(self, reason: str) -> None:
        self.state = AgentState.HALTED
        self._cancel_loops()  # stops NOW
        await self._emit(AlertType.CIRCUIT_BREAKER, "Circuit breaker triggered", reason)
        await self._liquidate_all()
        self._risk.halt()
        await self._emit(AlertType.PAUSED, "Capital protected in stablecoin", "Agent halted (READ_ONLY).")
        await self._publish_risk_state(halted_event=True)  # on-chain proof that the killswitch fired

    async def _liquidate_all(self) -> None:
        for pos in list(self.positions):
            await self._sell(pos, reason="liquidação")

    # ── boomerang / withdraw ─────────────────────────────────────────────────
    async def withdraw_all(self, to_address: str | None = None) -> None:
        dest = to_address or self._owner
        if not dest:
            await self._emit(AlertType.ERROR, "Withdraw failed", "Destination wallet not configured.")
            return
        await self._liquidate_all()
        try:
            stable = self._cfg.dev_safety["base_stable_symbol"]
            equity = await asyncio.to_thread(self._equity_usd)
            res = await asyncio.to_thread(
                self._executor.transfer_to_owner,
                to=dest, amount=equity, token=stable, password=self._password,
            )
            await self._emit(AlertType.WITHDRAWN, "Capital return executed",
                             f"Destination: {dest}", tx=res)
        except TwakError as exc:
            await self._emit(AlertType.ERROR, "Withdraw failed", str(exc))
        # "Withdraw All and Stop": after returning, halts the operation.
        if self.state != AgentState.HALTED:
            self.state = AgentState.PAUSED
            self._cancel_loops()
            await self._emit(AlertType.PAUSED, "Agent paused", "Withdraw complete. Use /start to resume.")

    # ── status ───────────────────────────────────────────────────────────────
    async def status(self) -> dict:
        try:
            equity = await asyncio.to_thread(self._equity_usd)
        except TwakError as exc:
            equity = None
            self._log.warning("portfolio unavailable: %s", exc)
        if equity is not None:
            self._last_equity = equity

        # Detail of each position with live PRICE and PnL (visibility for the user).
        positions_detail = []
        for p in self.positions:
            try:
                cur = await asyncio.to_thread(self._validator.onchain_price_usd, p.token_address)
            except Exception:  # noqa: BLE001
                cur = None
            pnl = ((cur - p.entry_price) / p.entry_price * 100.0) if cur else None
            positions_detail.append({
                "symbol": p.symbol, "amount_usd": p.amount_usd, "entry": p.entry_price,
                "current": cur, "pnl_pct": pnl, "stop": p.stop_loss_price,
                "take_profit": self._risk.take_profit_price(p.entry_price),
                "trailing_active": p.trailing_active, "projection": p.projection,
            })

        return {
            "state": self.state.value,
            "equity_usd": equity,
            "peak_equity_usd": self._risk.peak_equity,
            "drawdown_pct": self._risk.current_drawdown_pct(equity) if equity else None,
            "open_positions": [p.symbol for p in self.positions],
            "positions_detail": positions_detail,
            "token_focus": self.token_focus,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "position_size_pct": self.position_size_pct,
            "target_return_pct": self._cfg.dev_safety.get("target_return_pct", 0.0),
            "enable_ev_filter": self._cfg.dev_safety.get("enable_ev_filter", False),
            "posture": self._last_posture,
            "traces": list(self._last_traces[-12:]),
            "identity": identity.summary(),
        }

    # ── manual buy (validation / owner override) ─────────────────────────────
    async def force_buy(self, symbol: str, size_pct: float | None = None) -> None:
        """Forced buy of a token via the SAME real path (validation + TWAK).

        Ignores the LLM's verdict, but respects whitelist, slippage, risk and
        position sizing. The position is then monitored (stop/trailing) normally.

        size_pct (manual mode): explicit size in % of the bankroll (up to 100% = all-in),
        chosen by the owner and confirmed on Telegram. Without the automatic cap
        (max_position_pct); the drawdown circuit breaker and available stable still apply.
        If None, uses the configured automatic size.
        """
        symbol = symbol.upper()
        addr = self._addr(symbol)
        if not addr:
            await self._emit(AlertType.ERROR, "Manual buy failed", f"{symbol} outside the whitelist.")
            return
        now = time.time()
        try:
            equity = await asyncio.to_thread(self._equity_usd)
        except TwakError as exc:
            await self._emit(AlertType.ERROR, "Manual buy failed", str(exc))
            return
        self._risk.update_equity(equity)
        stable = self._stable_usd()  # real available USDC (not total equity)
        gate = self._risk.can_open_position(current_equity_usd=equity, available_stable_usd=stable,
                                            open_positions=len(self.positions), now_ts=now)
        if not gate.allowed:
            await self._emit(AlertType.ERROR, "Manual buy blocked", gate.detail)
            return
        size = self._risk.position_size_usd(equity, stable, override_pct=size_pct)
        if size <= 0:
            await self._emit(AlertType.ERROR, "Manual buy blocked",
                             f"Insufficient USDC (available ${stable:.2f}, minimum ${self._cfg.min_position_usd:.2f}).")
            return
        val = await asyncio.to_thread(
            self._validator.validate, symbol=symbol, token_address=addr, amount_usd=size)
        if not val.ok:
            await self._emit(AlertType.REJECTED, f"Manual buy barred: {symbol}", val.detail)
            return
        tag = f"manual buy {size_pct:.0f}%" if size_pct is not None else "manual buy"
        await self._open(symbol, addr, size, f"{tag} (validation)", now)

    async def sell_position(self, symbol: str) -> bool:
        """Manual sell of ONE open position (at market), by the owner via Telegram.
        Doesn't halt the agent (unlike /panic) — it keeps trading."""
        symbol = symbol.upper()
        pos = next((p for p in self.positions if p.symbol.upper() == symbol), None)
        if not pos:
            await self._emit(AlertType.ERROR, "Manual sell", f"No open position for {symbol}.")
            return False
        try:
            price = await asyncio.to_thread(self._validator.onchain_price_usd, pos.token_address)
        except Exception:  # noqa: BLE001
            price = None
        await self._sell(pos, reason="SELL_MANUAL", exit_price=price)
        return True

    async def sell_all_positions(self) -> int:
        """Sells ALL open positions at market (without halting the agent). Returns the count."""
        n = 0
        for pos in list(self.positions):
            if await self.sell_position(pos.symbol):
                n += 1
        return n

    async def register_competition(self) -> dict:
        """Registers the agent's wallet in the competition (twak compete register). On-chain,
        runs ONCE before the live week. Trades only count after registration."""
        return await asyncio.to_thread(self._executor.register_competition, self._password)

    async def competition_status(self) -> dict:
        """Queries the competition registration status (twak compete status)."""
        return await asyncio.to_thread(self._executor.competition_status, self._password)

    # ── loops ────────────────────────────────────────────────────────────────
    async def _scan_loop(self) -> None:
        interval = int(self._cfg.loop["scan_interval_seconds"])
        while self.state in (AgentState.SCANNING, AgentState.IN_POSITION):
            try:
                await self.run_cycle(time.time())
            except Exception as exc:  # noqa: BLE001
                self._log.exception("Error in scan: %s", exc)
                await self._emit(AlertType.ERROR, "Error in scan cycle", str(exc))
            await asyncio.sleep(interval)

    async def _monitor_loop(self) -> None:
        interval = int(self._cfg.loop["position_monitor_interval_seconds"])
        while self.state in (AgentState.SCANNING, AgentState.IN_POSITION):
            try:
                await self.check_positions()
            except Exception as exc:  # noqa: BLE001
                self._log.exception("Error in monitor: %s", exc)
            await asyncio.sleep(interval)

    # ── one scan cycle (testable) ────────────────────────────────────────────
    async def run_cycle(self, now: float) -> None:
        if self.state not in (AgentState.SCANNING, AgentState.IN_POSITION):
            return
        try:
            equity, reliable = await asyncio.to_thread(self._equity_usd_checked)
        except TwakError as exc:
            await self._emit(AlertType.ERROR, "Equity unavailable", str(exc))
            return

        # ANTI FALSE-CIRCUIT-BREAKER PROTECTION: if the reading isn't sound (limited RPC / position
        # with no price route), the equity comes deflated. We do NOT trip the circuit breaker nor
        # trade this cycle — a price hiccup must not liquidate the wallet by mistake.
        # The peak/anchor also don't update with bad data. Tries again next cycle.
        if not reliable:
            self._log.warning(
                "UNreliable wallet reading ($%.2f) — cycle skipped (RPC/price). "
                "Circuit breaker and trades suspended until the reading normalizes.", equity)
            return

        self._risk.update_equity(equity, now)
        self._last_equity = equity  # cache for dashboard
        await self._reconcile_positions()  # syncs tracking↔on-chain (clears test dust)
        stable = self._stable_usd()  # real available USDC to buy with (not total equity)
        if self._risk.circuit_breaker_tripped(equity):
            await self.panic(f"Drawdown {self._risk.current_drawdown_pct(equity):.1f}% hit the trigger.")
            return
        if self._risk.daily_loss_tripped(equity):
            await self.panic(f"Daily loss {self._risk.daily_drawdown_pct(equity):.1f}% hit the day's limit.")
            return
        if self._risk.too_many_buys(now):
            self._risk.halt()
            self.state = AgentState.HALTED
            await self._emit(AlertType.CIRCUIT_BREAKER, "Anomaly halt",
                             "rapid-buy pattern detected — agent frozen, manual review required")
            return

        if self._risk.needs_heartbeat(now):
            await self._heartbeat(now)

        await self._publish_risk_state()  # on-chain circuit breaker: attests state ~1x/hour (best-effort)

        # ECONOMY: 1 CMC call for ALL quotes (batch) + 1 global.
        global_metrics = await self._analyzer.gather_global()
        quotes = await self._analyzer.gather_quotes(self.token_focus)
        if not quotes:
            await self._emit(AlertType.DATA_ERROR, "No market data",
                             "Failed to fetch CMC quotes (REST). Check the API key / quota.")
            return

        # Attention Radar SKILL: adds the MARKET's biggest gainers that are in the
        # eligible whitelist (tradable) to the universe — catches surges outside the fixed basket.
        try:
            movers = await self._analyzer.gather_movers(set(self._token_addr.keys()), top_n=8)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Attention radar unavailable: %s", exc)
            movers = {}
        for sym, m in movers.items():
            quotes.setdefault(sym, m)
        universe = list(dict.fromkeys(list(self.token_focus) + list(movers.keys())))

        # MACRO FIRST (the strategy selector needs F&G to route panic→DCA).
        macro = await self._analyzer.gather_macro()
        btc24, fng, funding = macro.get("btc_24h"), macro.get("fng"), macro.get("funding")
        # x402 IN THE TRADE LOOP (load-bearing, ~1x/hour): pays a real micropayment on Base
        # (signed locally via twak) for CMC Agent Hub derivatives our REST plan blocks. The
        # data is fed to the brain below; on any failure we keep the Binance funding proxy.
        if now - self._last_x402 >= 3600:
            self._last_x402 = now
            deriv = await self._analyzer.gather_x402_derivatives()
            if deriv:
                self._x402_deriv = deriv if isinstance(deriv, dict) else {"cmc_derivatives": deriv}
                await self._emit(AlertType.STARTED, "💸 x402 paid in-loop (CMC derivatives)",
                                 "Self-custody micropayment on Base via TWAK — settles on-chain.")
        # Publishes the REAL market for the site demo to reuse (same process) — zero cost.
        market_cache.put(dict(quotes), btc24, fng)
        systemic = btc24 is not None and btc24 <= -5.0
        # DEPEG GUARD: if the trade stable (USDC) deviates from $1, the "safe cash" isn't
        # safe → blocks ALL entries and alerts the owner (does not liquidate).
        depeg = self._stable_depeg_bps(macro)
        depeg_block = depeg is not None and depeg >= self._cfg.stable_depeg_bps > 0
        if depeg_block:
            await self._maybe_alert_depeg(depeg)
        # Regime Adaptation SKILL: price (BTC) + SENTIMENT (fear/greed) + LEVERAGE.
        regime_label, cut_adjust = market_regime(btc24, fng, funding)
        # F&G #1 (extreme greed): tightens the exits of open positions (locks profit at the top).
        self._extreme_greed = fng is not None and fng >= 78
        # F&G #2 (DIRECTION, not just level): smooth EMA (~2.7h) reads whether sentiment is IMPROVING
        # (fear easing → bounce starting) or WORSENING (greed cooling → top turning).
        # The strongest signal is the turn, not the static value.
        if fng is not None:
            self._fng_ema = float(fng) if self._fng_ema is None else 0.97 * self._fng_ema + 0.03 * fng
            trend = fng - self._fng_ema
            if trend >= 4 and fng <= 55:          # fear easing / recovery starting
                cut_adjust -= 2
                regime_label += "↑"
            elif trend <= -4 and fng >= 50:        # greed cooling / top turning
                cut_adjust += 2
                regime_label += "↓"

        # Pre-score (only to rank/log) + STRATEGY SELECTION per token (the REAL trigger).
        prescores = [(s, momentum_prescore(quotes.get(s))) for s in universe if quotes.get(s)]
        ranked = [s for s, _ in sorted(prescores, key=lambda x: -x[1])]  # for rotation/log
        # PLAYBOOK: each token is routed to the strategy whose deterministic trigger fires
        # (momentum / mean-reversion / dca), according to regime + signals. In panic, DCA only.
        fired: list = []  # [(spec, symbol, strength)]
        for s in universe:
            mq = quotes.get(s)
            if not mq or self._exec_cooldown.get(s, 0.0) > now:
                continue
            spec = select_strategy(fng, mq, btc24, self._cfg.loose_entries)
            if spec:
                fired.append((spec, s, setup_strength(spec, mq)))
        fired.sort(key=lambda x: -x[2])
        # ACTION MATRIX + ARBITER: the regime dictates which strategies may open + the size/position
        # posture; a strategy bleeding (negative expectancy) is auto-deactivated from its history.
        posture = regime_posture(fng, btc24, funding)
        self._last_posture = posture.label
        disabled = expectancy_disabled([t for t in load_trades()
                                        if t.get("type") == "close" and t.get("pnl_pct") is not None])
        fired = [(spec, s, st) for (spec, s, st) in fired
                 if spec.key in posture.allowed and spec.key not in disabled]
        TOP_K = 3
        STRONG = 78  # clearly strong setup: stops spending calls and enters right away

        opened = False
        claude_calls = 0
        buys: list = []   # [(verdict, symbol, addr, spec)] of ALL that returned BUY
        top_eval = ""
        gate = self._risk.can_open_position(
            current_equity_usd=equity, available_stable_usd=stable,
            open_positions=len(self.positions), now_ts=now,
            max_positions=posture.max_positions,
        )
        # DCA is the PANIC strategy → ignores the BTC-plunging gate (it was MADE for that).
        # Depeg blocks everything; circuit breaker/cap/max-positions (gate) always still apply.
        active_is_dca = bool(fired) and fired[0][0].key == "dca"
        block = depeg_block or (systemic and not active_is_dca)
        gate_note = ""
        if depeg_block:
            gate_note = f" · DEPEG Gate: stable deviated {depeg/100:.2f}% (systemic risk)"
        elif block:
            gate_note = f" · MACRO Gate: BTC {btc24:+.1f}%/24h (systemic risk)"
        elif not gate.allowed:
            gate_note = f" · Gate BLOCKED: {gate.detail}"
        memory = self._performance_digest()  # Memory SKILL: history for the brain to calibrate
        self._last_traces = []  # fresh decision trace each cycle
        if not gate.allowed or block:
            self._trace("—", "REGIME/GATE", gate_note.strip(" ·") or "systemic gate")
        if gate.allowed and not block and fired:
            best_score = -1
            for spec, symbol, _strength in fired[:TOP_K]:
                addr = self._addr(symbol)
                if not addr:
                    continue
                # Mean-rev/DCA: the DEFENSIVE regime adjustment does NOT apply (the strategy IS already
                # the response to the regime). The brain CONFIRMS the setup in the active strategy's frame.
                ca = cut_adjust if spec.key == "momentum" else 0
                verdict = await self._analyzer.evaluate(
                    symbol, raw_metrics={**global_metrics, **self._x402_deriv, **quotes[symbol]},
                    memory=memory, cut_adjust=ca, strategy=spec.key)
                claude_calls += 1
                if verdict.confidence_score > best_score:
                    best_score = verdict.confidence_score
                    top_eval = f"{symbol} {verdict.confidence_score}{'✓' if verdict.is_buy else ''} [{spec.key}]"
                if verdict.is_buy:
                    buys.append((verdict, symbol, addr, spec))
                else:
                    self._trace(symbol, "BRAIN", f"score {verdict.confidence_score} — no setup [{spec.key}]")
                if verdict.is_buy and verdict.confidence_score >= STRONG:
                    break

            held = {p.symbol.upper() for p in self.positions}
            for verdict, symbol, addr, spec in sorted(buys, key=lambda b: -b[0].confidence_score):
                # Don't re-open a symbol already in the book: a second tracked position in the same
                # token would double exposure / concentration (the per-symbol cooldown alone lapses).
                if symbol.upper() in held:
                    self._trace(symbol, "GATE", "already in position — skip duplicate entry")
                    continue
                ch24 = float(quotes[symbol].get("percent_change_24h") or 0.0)
                # Anti-top + overextension dampening ONLY for MOMENTUM. Mean-rev and DCA
                # buy DROPS on purpose — penalizing them here would kill the strategy.
                if spec.key == "momentum" and ch24 > self._cfg.max_entry_24h_pct:
                    self._trace(symbol, "BRAIN", f"overextended 24h {ch24:+.1f}% — top risk")
                    await self._emit(AlertType.REJECTED, f"Entry barred (overextended): {symbol}",
                                     f"24h {ch24:+.1f}% > {self._cfg.max_entry_24h_pct:.0f}% — top risk.")
                    continue
                # ── Filter 1 timing gate: TA confluence on 1m candles (don't chase pumps) ──
                conf = await self._confluence(symbol, self._macro_label(posture.size_mult))
                if conf and conf.decision == "AVOID":
                    self._trace(symbol, "CONFLUENCE", conf.veto or conf.summary)
                    await self._emit(AlertType.REJECTED, f"Entry vetoed (TA): {symbol}",
                                     conf.veto or conf.summary)
                    continue
                conv_pct = conviction_size_pct(self.position_size_pct, verdict.confidence_score,
                                               max_pct=self._cfg.max_position_pct)
                if spec.key == "momentum":
                    conv_pct *= overextension_factor(ch24)
                conv_pct *= posture.size_mult  # ACTION MATRIX: regime scales the bet (defensive shrinks)
                if conf:  # CONFLUENCE: scale the bet by how much the TA agrees (cap respected later)
                    conv_pct *= 1.15 if conf.enter else (0.75 if conf.decision == "WAIT" else 1.0)
                size = self._risk.position_size_usd(equity, stable, override_pct=conv_pct)
                val = await asyncio.to_thread(
                    self._validator.validate, symbol=symbol, token_address=addr, amount_usd=size,
                    cmc_price_usd=quotes[symbol].get("price_usd"),  # ativa checagem de oráculo
                )
                if not val.ok:
                    self._trace(symbol, "VALIDATION", val.detail or "on-chain check failed")
                    await self._emit(AlertType.REJECTED, f"Trade barred: {symbol}",
                                     val.detail, reason=val.reason.value if val.reason else "")
                    continue
                # ERC-8004: SEALS the reasoning on-chain BEFORE executing (non-blocking).
                asyncio.create_task(self._commit_prediction(symbol, verdict, ch24, now))
                # TA STRATEGY (opt-in): refine the exit profile with the matching TA pattern.
                exec_spec = spec
                ta_spec = await self._ta_strategy(symbol)
                if ta_spec and ta_spec.key in posture.allowed:
                    exec_spec = ta_spec
                # EV PROJECTION: what the agent estimates this entry can reach (target/EV vs the
                # conviction-implied win rate). Surfaced on /live + Telegram; OPTIONALLY an entry
                # filter when the user sets a desired return (`target_return_pct` + `enable_ev_filter`).
                proj = project(target_pct=exec_spec.take_profit_pct, stop_pct=exec_spec.stop_pct,
                               conviction=verdict.confidence_score,
                               trailing_trigger_pct=exec_spec.trailing_trigger_pct)
                goal = float(self._cfg.dev_safety.get("target_return_pct", 0.0) or 0.0)
                if self._cfg.dev_safety.get("enable_ev_filter", False) and goal > 0 and proj.target_pct < goal:
                    self._trace(symbol, "GATE", f"projected +{proj.target_pct:.1f}% < target +{goal:.1f}%")
                    await self._emit(AlertType.REJECTED, f"Below target: {symbol}",
                                     f"Projected reach +{proj.target_pct:.1f}% < your target +{goal:.1f}%.")
                    continue
                # Opens with the STRATEGY's parameters (its own SL/TP/trailing/time-stop).
                rationale = f"[{exec_spec.label}] {verdict.rationale}"
                if conf and conf.summary:
                    rationale += f" · TA: {conf.summary}"
                if verdict.invalidation:
                    rationale += f" · Invalidated if: {verdict.invalidation}"
                if await self._open(symbol, addr, size, rationale, now,
                                    stop_pct=exec_spec.stop_pct, tp_pct=exec_spec.take_profit_pct,
                                    trailing_trigger_pct=exec_spec.trailing_trigger_pct,
                                    trailing_pct=exec_spec.trailing_pct, time_stop_min=exec_spec.time_stop_min,
                                    time_stop_band_pct=exec_spec.time_stop_band_pct,
                                    strategy=exec_spec.key, regime=verdict.regime,
                                    projection=proj.as_dict()):
                    opened = True
                    break
                # Execution failed (e.g.: revert due to liquidity): cooldown and try the next one.
                self._exec_cooldown[symbol] = now + 7200  # 2h off the radar
                self._log.info("%s in execution cooldown (2h) after swap failure.", symbol)

        # Opportunity Rotation SKILL: 100% allocated, but something MUCH better showed up? Rotates
        # out of the WEAKEST holding (never a winner on a run) to free up capital.
        rot_note = ""
        if (not opened and not block and self.positions
                and not gate.allowed and "insuficiente" in (gate.detail or "").lower()):
            rot_note = await self._maybe_rotate(equity, quotes, global_metrics, ranked, now)

        if opened:
            return  # TRADE_OPENED already notified
        top = sorted(prescores, key=lambda x: -x[1])[:3]
        top_str = " · ".join(f"{s} {sc}" for s, sc in top) or "—"
        melhor = f" · Best rated: {top_eval}" if top_eval else ""
        radar = f" · Radar: +{len(movers)} movers" if movers else ""
        reg = f" · Regime: {regime_label}({cut_adjust:+d}) · Posture: {posture.label} x{posture.size_mult:g}"
        if disabled:
            reg += f" · Disabled: {','.join(sorted(disabled))}"
        detail = (f"Analyzed {len(prescores)} tokens (momentum){radar}{reg}. Top: {top_str}. "
                  f"Candidates for AI: {len(fired)} · Claude: {claude_calls} call(s)."
                  f"{melhor}{gate_note}{rot_note} No entry.")
        self._log.info("CYCLE | %s", detail)  # visibility in the server log
        await self._emit(AlertType.SCAN, "Cycle complete", detail)
        self._save()

    async def _confluence(self, symbol: str, macro: str):
        """Pre-entry TA confluence gate over Binance 1m candles. Returns a Confluence,
        or None when the token has no candle data (then the gate is simply skipped —
        on-chain-only tokens are never penalized)."""
        try:
            klines = await asyncio.to_thread(fetch_klines, symbol, "1m", 60)
        except Exception:  # noqa: BLE001
            return None
        if not klines or len(klines) < 30:
            return None
        try:
            return evaluate_confluence(compute_indicators(klines), macro_regime=macro)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("confluence failed for %s: %s", symbol, exc)
            return None

    async def _ta_strategy(self, symbol: str):
        """Opt-in (config `enable_ta_strategies`): pick a refined TA strategy spec for an ENTER
        candidate from 5m klines (the timeframe where the backtest showed positive expectancy —
        1m bled to noise). None when disabled or no candle data. Additive."""
        if not self._cfg.dev_safety.get("enable_ta_strategies", False):
            return None
        try:
            klines = await asyncio.to_thread(fetch_klines, symbol, "5m", 60)
            if not klines or len(klines) < 30:
                return None
            return ta_select(compute_indicators(klines))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _macro_label(size_mult: float) -> str:
        if size_mult <= 0:
            return "RISK_OFF"
        if size_mult < 1:
            return "DEFENSIVE"
        return "BULL" if size_mult > 1 else "NEUTRAL"

    def _trace(self, symbol: str, stage: str, reason: str) -> None:
        """Record why a candidate did NOT enter (surfaced on /live, transparency)."""
        self._last_traces.append({"symbol": symbol, "blocked_at": stage, "reason": reason})
        self._last_traces = self._last_traces[-12:]

    async def _maybe_rotate(self, equity: float, quotes: dict, global_metrics: dict,
                            ranked: list, now: float) -> str:
        """Rotation SKILL: 100% allocated, but a HIGH-conviction candidate showed up? Sells the
        WEAKEST holding (one that is NOT a winner on a run) to free up capital. The buy
        enters next cycle (the sell's cooldown avoids churn). Returns a note for the log."""
        ROTATION_CUT = 72
        held = {p.symbol.upper() for p in self.positions}
        cand = next((s for s in ranked if self._addr(s) and s.upper() not in held), None)
        if not cand:
            return ""
        if float(quotes.get(cand, {}).get("percent_change_24h") or 0.0) > self._cfg.max_entry_24h_pct:
            return ""  # anti-top lock: doesn't rotate into an overextended token
        verdict = await self._analyzer.evaluate(
            cand, raw_metrics={**global_metrics, **self._x402_deriv, **quotes[cand]},
            memory=self._performance_digest())
        if not verdict.is_buy or verdict.confidence_score < ROTATION_CUT:
            return ""
        # weakest holding that is NOT a winner on a run (preserves the winners!)
        weakest = None
        weakest_pre, weakest_px = 999, 0.0
        for pos in self.positions:
            try:
                px = await asyncio.to_thread(self._validator.onchain_price_usd, pos.token_address)
            except Exception:  # noqa: BLE001
                continue
            pnl = ((px - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price else 0.0
            if pnl >= 5.0 or pos.trailing_active:  # winner on a run → NEVER rotates
                continue
            pre = momentum_prescore(quotes.get(pos.symbol)) if quotes.get(pos.symbol) else 0
            if pre < weakest_pre:
                weakest_pre, weakest, weakest_px = pre, pos, px
        if weakest is None or weakest_pre >= 15:  # only rotates if the holding is clearly weak
            return ""
        self._log.info("ROTATION | selling %s (weak pre=%d) → free capital for %s (score %d)",
                       weakest.symbol, weakest_pre, cand, verdict.confidence_score)
        await self._emit(AlertType.SCAN, "🔁 Capital rotation",
                         f"Exiting {weakest.symbol} (weak signal) to chase {cand} "
                         f"(score {verdict.confidence_score}). Buy next cycle.")
        await self._sell(weakest, reason="SELL_ROTACAO", exit_price=weakest_px)
        return f" · 🔁 Rotation: {weakest.symbol}→{cand}({verdict.confidence_score})"

    async def _open(self, symbol: str, addr: str, size_usd: float, rationale: str, now: float,
                    *, stop_pct: float = 0.0, tp_pct: float = 0.0, regime: str = "",
                    strategy: str = "", trailing_trigger_pct: float = 0.0, trailing_pct: float = 0.0,
                    time_stop_min: float = 0.0, time_stop_band_pct: float = 0.0,
                    projection: dict | None = None) -> bool:
        """Opens the position (real swap). Returns True if it opened, False if execution failed.

        With `strategy`, uses the STRATEGY's parameters literally (stop_pct=0 = NO SL,
        e.g.: DCA). Without strategy (manual buy), stop/tp fall back to the config fixed values."""
        def _do_buy():
            return self._executor.buy(to_token=addr, amount_usd=size_usd, password=self._password)
        with self._risk.trade_lock:
            res = await asyncio.to_thread(_do_buy)
        # 1st buy of a new token requires spend approval. twak sends the approval,
        # but the swap may revert BEFORE it mines ("Approval was sent... Check allowance
        # before retrying"). Detects this and retries ONCE after the approval confirms
        # (~20s, BSC blocks ~3s). The sleep stays OUTSIDE the lock so it doesn't block the stop monitor.
        if not res.ok and res.error and any(
                k in res.error.lower() for k in ("allowance", "approval was sent")):
            self._log.info("Approval sent for %s; waiting ~20s to mine and retrying swap.", symbol)
            await asyncio.sleep(20)
            with self._risk.trade_lock:
                res = await asyncio.to_thread(_do_buy)
        if not res.ok:
            await self._emit(AlertType.ERROR, f"Buy failed: {symbol}", res.error)
            return False
        entry = res.entry_price or await asyncio.to_thread(self._validator.onchain_price_usd, addr)
        # Effective SL/TP. With strategy: literal (stop=0 → NO SL, e.g.: DCA). Manual: config fixed.
        if strategy:
            eff_stop, eff_tp = stop_pct, tp_pct
        else:
            eff_stop = stop_pct or self.stop_loss_pct
            eff_tp = tp_pct or self.take_profit_pct
        stop_price = entry * (1.0 - eff_stop / 100.0) if eff_stop > 0 else 0.0  # 0 = no SL (global circuit breaker covers it)
        tp_price = entry * (1.0 + eff_tp / 100.0) if eff_tp > 0 else 0.0
        pos = Position(symbol=symbol, token_address=addr, entry_price=entry, amount_usd=size_usd,
                       qty=res.qty or 0.0, stop_loss_price=stop_price,
                       stop_loss_pct=eff_stop, take_profit_pct=eff_tp,
                       opened_at=now, tx_hash=res.tx_hash, regime=regime, strategy=strategy,
                       trailing_trigger_pct=trailing_trigger_pct, trailing_pct=trailing_pct,
                       time_stop_min=time_stop_min, time_stop_band_pct=time_stop_band_pct,
                       projection=projection)
        self.positions.append(pos)
        self._risk.record_buy(now)  # anomaly tripwire (rapid-buy / injection cascade)
        self.state = AgentState.IN_POSITION
        self._risk.record_trade(now)
        vol_note = f" | vol {pos.stop_loss_pct:.1f}/{pos.take_profit_pct:.1f}%" if stop_pct else ""
        self._log.info("TRADE OPENED | %s $%.2f @ %.8g%s | tx=%s",
                       symbol, size_usd, entry, vol_note, res.tx_hash)
        append_trade({"type": "open", "symbol": symbol, "amount_usd": size_usd,
                      "entry_price": entry, "tx": res.tx_hash, "ts": now, "regime": regime,
                      "strategy": strategy})
        await self._emit(AlertType.TRADE_OPENED, f"Position opened: {symbol}",
                         rationale, amount_usd=size_usd, entry=entry,
                         stop=stop_price, take_profit=tp_price,
                         stop_pct=eff_stop, take_profit_pct=eff_tp,
                         projection=projection, tx=res.tx_hash)
        self._save()
        return True

    # ── position monitor (stop / trailing) ───────────────────────────────────
    async def check_positions(self) -> None:
        for pos in list(self.positions):
            try:
                price = await asyncio.to_thread(self._validator.onchain_price_usd, pos.token_address)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("price unavailable %s: %s", pos.symbol, exc)
                continue
            # Record the live price/PnL so the public /live panel shows it (parity with Telegram).
            pos.last_price = price
            pos.last_pnl_pct = (((price - pos.entry_price) / pos.entry_price * 100.0)
                                if pos.entry_price else None)
            signal = self._risk.evaluate_position(pos, price, tighten=self._extreme_greed)
            if signal != ExitSignal.HOLD:
                await self._sell(pos, reason=signal.value, exit_price=price)
            else:
                # Smart Exit SKILL: if the mechanical check says HOLD, the brain re-evaluates the
                # thesis (every ~5min) and may exit early — protect profit / cut a reversal.
                await self._smart_exit_check(pos, price)
        # Persist the refreshed live PnL (throttled) so /live stays fresh without flogging the disk.
        if self.positions:
            now = time.time()
            if now - self._last_mon_save >= 10:
                self._last_mon_save = now
                self._save()
        if not self.positions and self.state == AgentState.IN_POSITION:
            self.state = AgentState.SCANNING

    async def _smart_exit_check(self, pos: Position, price: float) -> bool:
        """Qualitative re-evaluation of the position by the brain (throttled). Exits if the thesis
        broke (momentum turned / volume dried up / overextended), regardless of the stop."""
        now = time.time()
        # Minimum hold before the DISCRETIONARY brain exit can fire: give the thesis room to
        # play out instead of scratching a position in ~75s and paying round-trip fees for nothing.
        # The mechanical stop/trailing/time-stop (check_positions) still protect against real moves.
        min_hold = float(self._cfg.dev_safety.get("min_hold_min_before_brain_exit", 0.0))
        if min_hold > 0 and (now - pos.opened_at) / 60.0 < min_hold:
            return False
        if now - self._exit_checks.get(pos.symbol, 0.0) < 300:  # 1 re-evaluation / 5min / position
            return False
        self._exit_checks[pos.symbol] = now
        pnl = ((price - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price else 0.0
        try:
            decision = await self._analyzer.evaluate_exit(
                pos.symbol, pnl_pct=pnl, held_min=(now - pos.opened_at) / 60.0)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Smart exit unavailable (%s): %s", pos.symbol, exc)
            return False
        if decision.should_exit:
            self._log.info("SMART EXIT | %s (PnL %+.2f%%): %s", pos.symbol, pnl, decision.reason)
            await self._sell(pos, reason="SELL_BRAIN", exit_price=price)
            return True
        return False

    async def _sell(self, pos: Position, *, reason: str, exit_price: float | None = None) -> None:
        # Sells the REAL on-chain balance (not the tracked qty, which may be stale due to
        # dust/fee-on-transfer) with 0.1% slack — avoids "transfer amount exceeds balance".
        try:
            raw = await asyncio.to_thread(self._validator._token_balance, pos.token_address, self.agent_address)
            dec = await asyncio.to_thread(self._validator._decimals, pos.token_address)
            real_qty = raw / (10 ** dec)
        except Exception:  # noqa: BLE001
            real_qty = pos.qty
        px = exit_price or pos.entry_price or 0.0
        if real_qty <= 0 or real_qty * px < 0.10:
            # Only dust remained → the position has effectively already exited; unlock the tracking.
            if pos in self.positions:
                self.positions.remove(pos)
            self._save()
            self._log.info("%s already liquidated on-chain (dust); removed from tracking.", pos.symbol)
            await self._emit(AlertType.TRADE_CLOSED, f"Position closed: {pos.symbol}",
                             "Was already sold (only on-chain dust remained).", reason=reason,
                             pnl_pct=None, entry=pos.entry_price, exit=px, amount_usd=pos.amount_usd, tx=None)
            if not self.positions and self.state == AgentState.IN_POSITION:
                self.state = AgentState.SCANNING
            return
        sell_qty = real_qty * 0.999

        def _do_sell():
            return self._executor.sell_all(
                token=pos.token_address, amount=sell_qty, password=self._password)
        with self._risk.trade_lock:
            res = await asyncio.to_thread(_do_sell)
        # 1st sell of a token requires approval (token→router); the swap may revert before
        # it mines. Retries ONCE after ~20s (same as the buy). Sleep outside the lock.
        if not res.ok and res.error and any(
                k in res.error.lower() for k in ("allowance", "approval was sent")):
            self._log.info("Sell approval sent for %s; waiting ~20s and retrying.", pos.symbol)
            await asyncio.sleep(20)
            with self._risk.trade_lock:
                res = await asyncio.to_thread(_do_sell)
        if not res.ok:
            # Does NOT remove the position: the token stays in the wallet → remains managed and
            # sellable (avoids becoming an orphan holding without a stop). Just reports the failure.
            self._log.warning("Sell of %s failed: %s (position kept).", pos.symbol, res.error)
            await self._emit(AlertType.ERROR, f"Sell failed: {pos.symbol}",
                             (res.error or "") + " — position kept; try again.")
            return
        if pos in self.positions:
            self.positions.remove(pos)
        self._risk.record_trade(time.time())
        self._log.info("TRADE CLOSED | %s reason=%s | tx=%s", pos.symbol, reason, res.tx_hash)
        pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price * 100.0) if exit_price else None
        append_trade({"type": "close", "symbol": pos.symbol, "reason": reason,
                      "pnl_pct": pnl_pct, "tx": res.tx_hash, "ts": time.time(),
                      "regime": pos.regime})
        await self._emit(AlertType.TRADE_CLOSED, f"Position closed: {pos.symbol}",
                         "", reason=reason, pnl_pct=pnl_pct,
                         entry=pos.entry_price, exit=exit_price, amount_usd=pos.amount_usd,
                         tx=res.tx_hash)
        self._save()
        await self._publish_reputation()  # aggregate track_record (throttled ~1x/hour)
        # ERC-8004 reputation model: per-trade signed feedback (additive, non-blocking).
        asyncio.create_task(self._publish_trade_feedback(pos.symbol, pnl_pct, pos.regime))

    def _performance_digest(self) -> str:
        """Memory SKILL: summary of the agent's OWN history for the brain to calibrate (learns from
        what it did). Win-rate, average PnL, recent results and tokens that bled."""
        closes = [t for t in load_trades() if t.get("type") == "close" and t.get("pnl_pct") is not None]
        if len(closes) < 3:
            return ""  # short history → don't bias
        recent = closes[-20:]
        wins = sum(1 for t in recent if t["pnl_pct"] > 0)
        wr = wins / len(recent) * 100.0
        avg = sum(t["pnl_pct"] for t in recent) / len(recent)
        last5 = "".join("V" if t["pnl_pct"] > 0 else "D" for t in recent[-5:])
        from collections import defaultdict
        by_sym: dict[str, list] = defaultdict(list)
        for t in recent:
            by_sym[t["symbol"]].append(t["pnl_pct"])
        sangra = sorted(((s, sum(v) / len(v)) for s, v in by_sym.items()
                         if len(v) >= 2 and sum(v) / len(v) < -1.0), key=lambda x: x[1])[:3]
        note = (f"YOUR HISTORY ({len(recent)} trades): {wr:.0f}% win rate, average PnL "
                f"{avg:+.1f}%, last 5: {last5}.")
        # WIN-RATE BY REGIME: shows the brain WHERE it wins and where it bleeds (e.g.: chop).
        by_reg: dict[str, list] = defaultdict(list)
        for t in recent:
            r = (t.get("regime") or "").strip().lower()
            if r:
                by_reg[r].append(t["pnl_pct"])
        reg_parts = [f"{r} {sum(1 for x in v if x>0)/len(v)*100:.0f}%% ({len(v)})"
                     for r, v in by_reg.items() if len(v) >= 2]
        if reg_parts:
            note += " By REGIME: " + ", ".join(reg_parts).replace("%%", "%") + "."
            chop = by_reg.get("choppy", [])
            if len(chop) >= 3 and sum(1 for x in chop if x > 0) / len(chop) < 0.4:
                note += " You LOSE in choppy — be much more selective (or WAIT) in the sideways market."
        if sangra:
            note += (" Tokens that bled you: "
                     + ", ".join(f"{s}({a:+.0f}%)" for s, a in sangra)
                     + " — be MORE selective on them.")
        note += " Use this to calibrate conviction: if you've been losing, raise the bar; if winning, trust it."
        return note

    async def _commit_prediction(self, symbol: str, verdict, ch24: float, now: float) -> None:
        """Seals the reasoning ON-CHAIN before execution (anti-fabrication). NON-BLOCKING
        and best-effort: runs in parallel to the swap; if it fails, the trade happens anyway."""
        pred = {"symbol": symbol, "score": verdict.confidence_score,
                "volatility": verdict.volatility, "ch24": round(ch24, 1),
                "rationale": verdict.rationale[:220], "invalidation": verdict.invalidation[:150],
                "ts": int(now)}
        try:
            res = await asyncio.to_thread(identity.commit_prediction, pred)
            if res:
                self._log.info("PRE-COMMIT on-chain | %s hash=%s tx=%s",
                               symbol, res["hash"][:14], res.get("tx"))
                await self._emit(AlertType.STARTED, "🔒 Reasoning sealed on-chain (pre-execution)",
                                 f"{symbol}: the thesis was recorded on BNB Chain BEFORE the trade — "
                                 "verifiable proof, can't be made up afterwards.", tx=res.get("tx"))
                self._last_seal = {"symbol": symbol, "tx": res.get("tx"),
                                   "hash": res["hash"][:14], "ts": int(now)}
                self._save()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("On-chain pre-commit failed (ignored): %s", exc)

    async def _publish_reputation(self) -> None:
        """On-chain Reputation SKILL: publishes the history (trades/win-rate/PnL) as
        verifiable ERC-8004 metadata. Throttled to ~1x/hour; best-effort (gas/network)."""
        now = time.time()
        if now - self._last_rep_publish < 3600:
            return
        closes = [t for t in load_trades() if t.get("type") == "close" and t.get("pnl_pct") is not None]
        if not closes:
            return
        # Back off even on FAILURE (don't retry a reverting write every cycle).
        self._last_rep_publish = now
        wins = sum(1 for t in closes if t["pnl_pct"] > 0)
        stats = {
            "trades": len(closes), "wins": wins,
            "win_rate": round(wins / len(closes) * 100, 1),
            "total_pnl_pct": round(sum(t["pnl_pct"] for t in closes), 2),
            "ts": int(now),
        }
        try:
            res = await asyncio.to_thread(identity.publish_track_record, stats)
            if res:
                await self._emit(AlertType.STARTED, "🏅 On-chain reputation updated",
                                 f"{stats['trades']} trades · {stats['win_rate']:.0f}% win · "
                                 f"cum PnL {stats['total_pnl_pct']:+.2f}%",
                                 tx=res.get("transactionHash"))
                self._log.info("On-chain reputation published: %s (tx=%s)", stats, res.get("transactionHash"))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Reputation publication failed (ignored): %s", exc)

    async def _publish_trade_feedback(self, symbol: str, pnl_pct: float | None, regime: str) -> None:
        """ERC-8004 reputation model: after a close, publish a signed per-trade feedback
        (value = realized yield in bps, tag = tradingYield). Additive to the aggregate
        track_record; best-effort, never interrupts trading."""
        if pnl_pct is None:
            return
        feedback = {"value": round(pnl_pct * 100), "valueDecimals": 2, "tag1": "tradingYield",
                    "symbol": symbol, "regime": regime, "ts": int(time.time())}
        try:
            res = await asyncio.to_thread(identity.publish_reputation, feedback)
            if res:
                self._log.info("Per-trade reputation feedback published: %s (tx=%s)",
                               symbol, res.get("transactionHash"))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Trade feedback publish failed (ignored): %s", exc)

    async def _publish_risk_state(self, *, halted_event: bool = False) -> None:
        """ON-CHAIN CIRCUIT BREAKER: attests the circuit breaker state on ERC-8004 (key
        'risk_state'). Throttled ~1x/hour, BUT always when the killswitch fires
        (halted_event) — that leaves on-chain proof that the lock acted. Best-effort."""
        now = time.time()
        if not halted_event and now - self._last_risk_publish < 3600:
            return
        equity = self._last_equity or 0.0
        if equity <= 0 and not halted_event:
            return
        # Back off even on FAILURE: a persistently reverting write (e.g. on-chain "Not
        # authorized") must not be retried every cycle. Advance the timer at the attempt.
        self._last_risk_publish = now
        peak = self._risk.peak_equity

        def to_bps(pct: float | None) -> int:  # 1% = 100 bps
            return int(round((pct or 0.0) * 100))
        state = {
            "peak": round(peak, 2), "equity": round(equity, 2),
            "drawdown_bps": to_bps(self._risk.current_drawdown_pct(equity)),
            "daily_bps": to_bps(self._risk.daily_drawdown_pct(equity)),
            "max_bps": to_bps(self._cfg.drawdown_safety_pct),
            "daily_cap_bps": to_bps(self._cfg.daily_loss_cap_pct),
            "halted": self._risk.halted, "ts": int(now),
        }
        try:
            res = await asyncio.to_thread(identity.publish_risk_state, state)
            if res:
                self._log.info("Circuit breaker attested on-chain: %s (tx=%s)", state, res.get("transactionHash"))
                if halted_event:
                    await self._emit(AlertType.CIRCUIT_BREAKER, "🔒 Circuit breaker sealed on-chain",
                                     f"Drawdown {state['drawdown_bps']/100:.1f}% — ERC-8004 proof",
                                     tx=res.get("transactionHash"))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("On-chain attestation of the circuit breaker failed (ignored): %s", exc)

    # ── heartbeat (minimum trades) ───────────────────────────────────────────
    async def _heartbeat(self, now: float) -> None:
        """Maintenance swap ~$1 USDT→USDC to meet the minimum trades/day.

        Zero market risk (stable↔stable). Real in live mode; simulated on paper.
        """
        # Heartbeat = swap from the base stable to the OTHER stable (~zero market risk).
        base_sym = self._cfg.dev_safety["base_stable_symbol"].upper()
        target = (self._cfg.network.get("usdt_bsc_address") if base_sym == "USDC"
                  else self._cfg.network.get("usdc_bsc_address"))
        if target:
            try:
                res = await asyncio.to_thread(
                    self._executor.buy, to_token=target, amount_usd=1.05, password=self._password)
                detail = (f"Maintenance swap stable↔stable (~$1): "
                          f"{'ok ' + (res.tx_hash or '') if res.ok else 'failed: ' + res.error}")
            except Exception as exc:  # noqa: BLE001
                detail = f"Heartbeat failed: {exc}"
        else:
            detail = "Target stable address not configured."
        self._risk.record_trade(now)
        await self._emit(AlertType.HEARTBEAT, "Heartbeat trade", detail)
        self._save()
