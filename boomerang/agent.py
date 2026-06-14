"""Orquestrador do Boomerang AI — o loop que une os três filtros + motor de risco.

Process B (lógica do agente). NÃO conhece o Telegram; só emite alertas pelo
AlertBus. A interface envia intents de controle (configure/pause/panic/withdraw).

Loop de scan (intervalo do config) → Filtro 1 (CMC) → Filtro 2 (BNB) → Filtro 3
(TWAK). Loop de monitor (2s) → stop-loss / trailing por posição.
Todas as chamadas externas síncronas rodam em thread para não travar o loop.
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
from boomerang.persistence import append_trade, load_state, load_trades, save_state
from boomerang.risk import RiskEngine
from boomerang.risk.risk_engine import ExitSignal
from boomerang.strategy.playbook import select_strategy, setup_strength
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

        # camada do usuário (ajustável via Telegram)
        self.token_focus: list[str] = list(config.user.get("token_focus", []))
        # cesta curada original (preservada antes do restore(), p/ o botão "recomendada"
        # restaurar amplitude se o foco tiver sido reduzido a 1 moeda)
        self._default_focus: list[str] = list(config.user.get("token_focus", []))
        self._default_size: float = config.user_position_size_pct  # tamanho default p/ reset
        self.stop_loss_pct: float = config.user_stop_loss_pct
        self.take_profit_pct: float = config.user_take_profit_pct
        self.position_size_pct: float = config.user_position_size_pct
        self.mode: str = config.user.get("mode", "conservative")
        self._tasks: list[asyncio.Task] = []
        # tokens cuja EXECUÇÃO falhou (ex.: revert por liquidez): symbol -> expiry ts.
        # O agente os ignora por um tempo e roteia p/ candidatos tradáveis (auto-aprende).
        self._exec_cooldown: dict[str, float] = {}
        # Skill Saída Inteligente: última reavaliação do cérebro por posição (throttle).
        self._exit_checks: dict[str, float] = {}
        # Skill Reputação on-chain: timestamp da última publicação (throttle ~1x/hora).
        self._last_rep_publish: float = 0.0
        self._last_risk_publish: float = 0.0
        self._last_depeg_alert: float = 0.0
        self._extreme_greed: bool = False     # F&G >= 78 → aperta exits (trava lucro no topo)
        self._fng_ema: float | None = None    # média suave do F&G p/ ler a DIREÇÃO (medo aliviando vs esfriando)
        self._last_equity: float = 0.0          # patrimônio mais recente (cache p/ dashboard)
        self._last_holdings: list = []          # composição por moeda (cache p/ painel /live)
        self.agent_address: str | None = None   # endereço da carteira (preenchido no startup)

    # ── carregamento de endereços elegíveis ──────────────────────────────────
    def _load_token_map(self) -> dict[str, str]:
        path = ROOT / self._cfg.hackathon["eligible_tokens_file"]
        # Exclui as stables base (USDC/USDT): elas NÃO são alvos de trade, e contá-las
        # como "token" duplicaria o saldo na composição (elas já entram como 'stable').
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
        # Visibilidade no log do servidor p/ eventos que explicam por que NÃO operou
        # ou que exigem atenção (sem isso o motivo só ia ao Telegram — ponto cego).
        if type_ in (AlertType.REJECTED, AlertType.ERROR, AlertType.DATA_ERROR,
                     AlertType.CIRCUIT_BREAKER):
            self._log.info("%s | %s — %s", type_.name, title, detail)
        await self._alerts.emit(Alert(type_, title, detail, data))

    # ── medição de patrimônio (equity) ───────────────────────────────────────
    def _equity_usd_checked(self) -> tuple[float, bool]:
        """Patrimônio total em USD lido ON-CHAIN + flag de CONFIABILIDADE.

        A leitura é NÃO-confiável quando uma posição aberta não pôde ser precificada
        (RPC limitado / sem rota de preço) — aí a equity vem deflacionada e NÃO deve
        disparar o disjuntor de drawdown. Cai no portfolio do TWAK (fonte de preço
        própria, tratada como confiável) só se a leitura on-chain falhar inteira.
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
                self._log.warning("Equity on-chain indisponível (%s); usando TWAK.", exc)
        return self._executor.portfolio_usd(self._password), True

    def _equity_usd(self) -> float:
        """Patrimônio total em USD (sem a flag — p/ status/saque/heartbeat)."""
        return self._equity_usd_checked()[0]

    def _stable_usd(self) -> float:
        """Saldo da stable de trade (base_stable, ex.: USDC) em USD — o quanto o
        agente REALMENTE pode gastar numa compra. Diferente da equity total (que
        inclui posições abertas e gás). Lido do breakdown on-chain mais recente
        (_last_holdings, atualizado por _equity_usd). Evita tentar comprar mais
        stable do que existe (causa de swap revertido por saldo insuficiente)."""
        base = str(self._cfg.dev_safety.get("base_stable_symbol", "USDC")).upper()
        for h in self._last_holdings:
            if str(h.get("symbol", "")).upper() == base:
                return float(h.get("value_usd") or 0.0)
        return 0.0

    def _stable_depeg_bps(self, macro: dict) -> float | None:
        """Desvio (em bps) da stable de TRADE em relação a $1, da referência CMC.
        None se o preço não veio. base USDC → usdc_price; base USDT → usdt_price."""
        base = str(self._cfg.dev_safety.get("base_stable_symbol", "USDC")).upper()
        price = macro.get("usdc_price") if base == "USDC" else macro.get("usdt_price")
        if not price or price <= 0:
            return None
        return abs(price - 1.0) * 10000.0

    async def _maybe_alert_depeg(self, depeg_bps: float) -> None:
        """Alerta de depeg (throttled ~1x/30min) — não spamma a cada ciclo."""
        now = time.time()
        if now - self._last_depeg_alert < 1800:
            return
        self._last_depeg_alert = now
        base = str(self._cfg.dev_safety.get("base_stable_symbol", "USDC")).upper()
        await self._emit(AlertType.CIRCUIT_BREAKER, "🛡️ Depeg detectado",
                         f"{base} desviou {depeg_bps/100:.2f}% de $1 — entradas bloqueadas "
                         f"até reancorar. Capital protegido, sem novas compras.")

    async def _reconcile_positions(self) -> None:
        """Sincroniza o tracking com a carteira REAL on-chain, nos dois sentidos:
        (1) descarta posições rastreadas que não existem mais on-chain (vendas por fora
            / pó), comparando o SALDO REAL do token (não o valor em USD, sujeito a
            preço-lixo de liquidez fina);
        (2) IMPORTA holdings de tokens elegíveis que estão na carteira mas NÃO são
            rastreados (sobras de teste/restart) — senão ficam sem stop-loss/gestão."""
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
                self._log.warning("Reconciliação: não checou %s (%s); mantendo.", pos.symbol, exc)
                kept.append(pos)
                continue
            # Poeira (valor on-chain desprezível, resto de venda) → destrava o tracking.
            try:
                price = await asyncio.to_thread(self._validator.onchain_price_usd, pos.token_address)
                if onchain * price < 0.20:
                    dropped.append(pos.symbol)
                    continue
            except Exception:  # noqa: BLE001
                pass
            if pos.qty > 0 and onchain >= pos.qty * 0.20:  # ainda há o grosso da posição
                kept.append(pos)
            else:
                dropped.append(pos.symbol)
        if dropped:
            self.positions = kept
            self._save()
            self._log.info("Reconciliação: descartei %d posição(oes) sem saldo on-chain: %s",
                           len(dropped), ", ".join(dropped))
        await self._import_orphans()

    async def _import_orphans(self) -> None:
        """Importa holdings on-chain de tokens elegíveis NÃO rastreados (sobras de
        teste/restart), passando a gerenciá-los (stop-loss/trailing). Entry = preço
        atual: a proteção passa a valer a partir de agora."""
        if not self.agent_address:
            return
        try:
            bd = await asyncio.to_thread(self._validator.wallet_breakdown, self.agent_address)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Import de órfãos: breakdown indisponível (%s).", exc)
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
            self._log.info("Importei %d holding(s) órfão(s) p/ gestão: %s", len(imported), ", ".join(imported))
            await self._emit(AlertType.STARTED, "📥 Posições importadas p/ gestão",
                             f"Agora monitorando (stop-loss): {', '.join(imported)}")

    # ── controle (chamado pela interface) ────────────────────────────────────
    def configure(self, *, token_focus: list[str] | None = None,
                  stop_loss_pct: float | None = None, take_profit_pct: float | None = None,
                  position_size_pct: float | None = None, mode: str | None = None) -> None:
        # Propaga para cfg.user (fonte única lida pelo analyzer e pelo motor de risco).
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

    # ── persistência (sobrevive a reinício) ──────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "token_focus": self.token_focus,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "position_size_pct": self.position_size_pct,
            "mode": self.mode,
            "peak_equity": self._risk.peak_equity,
            "equity_usd": self._last_equity,
            "drawdown_pct": (self._risk.current_drawdown_pct(self._last_equity)
                             if self._last_equity else 0.0),
            "last_trade_ts": self._risk.last_trade_ts,
            "agent_address": self.agent_address,
            "holdings": self._last_holdings,
            "positions": [asdict(p) for p in self.positions],
            "identity": identity.summary(),
        }

    def _save(self) -> None:
        try:
            save_state(self.snapshot())
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Falha ao salvar estado: %s", exc)

    def restore(self) -> bool:
        """Carrega estado salvo (se houver). Retorna True se restaurou."""
        data = load_state()
        if not data:
            return False
        try:
            self.state = AgentState(data.get("state", self.state.value))
        except ValueError:
            pass
        self.token_focus = data.get("token_focus", self.token_focus)
        # Reset cirúrgico (BOOMERANG_RESET_FOCUS=1): ignora foco/tamanho salvos de testes
        # e restaura os defaults do config (cesta líquida + 25%) — sem mexer no stop/lucro.
        # Destrava o autônomo sem reconfigurar tudo no bot.
        if os.getenv("BOOMERANG_RESET_FOCUS", "").strip() in ("1", "true", "True") and self._default_focus:
            self.token_focus = list(self._default_focus)
            self.position_size_pct = self._default_size  # também volta ao tamanho default (25%)
            data["position_size_pct"] = self._default_size  # não deixa o restore sobrescrever abaixo
            data["peak_equity"] = 0.0  # rebaseia o pico (limpa peak fantasma) → drawdown 0% no 1º ciclo
            self._log.info("Reset: foco→cesta (%d moedas), tamanho→%.0f%%, peak rebaseado.",
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
        self._log.info("Estado restaurado: %d posicao(oes), pico $%.2f",
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
            await self._emit(AlertType.ERROR, "Agente travado",
                             "Circuit breaker ativo (panic/saque). Use */reiniciar* para destravar e voltar a operar.")
            return
        # Sincroniza com a carteira: descarta posições já vendidas/pó antes de operar.
        await self._reconcile_positions()
        # Mantém IN_POSITION se já há posição aberta (ex.: retomada após restart).
        self.state = AgentState.IN_POSITION if self.positions else AgentState.SCANNING
        await self._emit(AlertType.STARTED, "Boomerang AI ativo",
                         f"Foco: {', '.join(self.token_focus)} | tamanho {self.position_size_pct:.0f}% | "
                         f"stop {self.stop_loss_pct}% | modo {self.mode}")
        self._start_loops()

    async def pause(self) -> None:
        self.state = AgentState.PAUSED
        self._cancel_loops()  # para AGORA (não espera o ciclo terminar/dormir)
        await self._emit(AlertType.PAUSED, "Agente pausado", "Varredura interrompida. Use /start para retomar.")

    async def resume(self) -> None:
        if self.state == AgentState.PAUSED:
            self.state = AgentState.SCANNING
            await self._emit(AlertType.STARTED, "Agente retomado")
            self._start_loops()

    async def restart_session(self) -> None:
        """Destrava após HALTED (panic/saque/disjuntor) e volta a operar. Limpa o circuit
        breaker, rebaseia o pico no patrimônio atual (fresh start) e reconcilia a carteira.
        Uso consciente do dono via /reiniciar."""
        self._risk.clear_halt()
        try:
            eq = await asyncio.to_thread(self._equity_usd)
            if eq > 0:
                self._risk.restore_state(eq, self._risk.last_trade_ts)  # rebaseia o pico
                self._last_equity = eq
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Reinício: equity indisponível (%s).", exc)
        await self._reconcile_positions()
        self.state = AgentState.IN_POSITION if self.positions else AgentState.SCANNING
        self._save()
        await self._emit(AlertType.STARTED, "🔄 Sessão reiniciada",
                         "Travamento limpo, pico rebaseado. Operando novamente.")
        self._start_loops()

    async def stop(self) -> None:
        self._cancel_loops()

    # ── disjuntor / pânico ───────────────────────────────────────────────────
    async def panic(self, reason: str) -> None:
        self.state = AgentState.HALTED
        self._cancel_loops()  # para AGORA
        await self._emit(AlertType.CIRCUIT_BREAKER, "Disjuntor acionado", reason)
        await self._liquidate_all()
        self._risk.halt()
        await self._emit(AlertType.PAUSED, "Capital protegido em stablecoin", "Agente travado (READ_ONLY).")
        await self._publish_risk_state(halted_event=True)  # prova on-chain de que o killswitch disparou

    async def _liquidate_all(self) -> None:
        for pos in list(self.positions):
            await self._sell(pos, reason="liquidação")

    # ── boomerang / saque ────────────────────────────────────────────────────
    async def withdraw_all(self, to_address: str | None = None) -> None:
        dest = to_address or self._owner
        if not dest:
            await self._emit(AlertType.ERROR, "Saque falhou", "Carteira de destino não configurada.")
            return
        await self._liquidate_all()
        try:
            stable = self._cfg.dev_safety["base_stable_symbol"]
            equity = await asyncio.to_thread(self._equity_usd)
            res = await asyncio.to_thread(
                self._executor.transfer_to_owner,
                to=dest, amount=equity, token=stable, password=self._password,
            )
            await self._emit(AlertType.WITHDRAWN, "Devolução de capital executada",
                             f"Destino: {dest}", tx=res)
        except TwakError as exc:
            await self._emit(AlertType.ERROR, "Saque falhou", str(exc))
        # "Sacar Tudo e Parar": após devolver, interrompe a operação.
        if self.state != AgentState.HALTED:
            self.state = AgentState.PAUSED
            self._cancel_loops()
            await self._emit(AlertType.PAUSED, "Agente pausado", "Saque concluído. Use /start para retomar.")

    # ── status ───────────────────────────────────────────────────────────────
    async def status(self) -> dict:
        try:
            equity = await asyncio.to_thread(self._equity_usd)
        except TwakError as exc:
            equity = None
            self._log.warning("portfolio indisponível: %s", exc)
        if equity is not None:
            self._last_equity = equity

        # Detalhe de cada posição com PREÇO e PnL ao vivo (visibilidade p/ o usuário).
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
                "trailing_active": p.trailing_active,
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
        }

    # ── compra manual (validação / override do dono) ─────────────────────────
    async def force_buy(self, symbol: str, size_pct: float | None = None) -> None:
        """Compra forçada de um token pelo MESMO caminho real (validação + TWAK).

        Ignora o veredito do LLM, mas respeita whitelist, slippage, risco e
        position sizing. A posição passa a ser monitorada (stop/trailing) normalmente.

        size_pct (modo manual): tamanho explícito em % da banca (até 100% = all-in),
        escolhido pelo dono e confirmado no Telegram. Sem o teto automático
        (max_position_pct); o disjuntor de drawdown e o stable disponível seguem valendo.
        Se None, usa o tamanho automático configurado.
        """
        symbol = symbol.upper()
        addr = self._addr(symbol)
        if not addr:
            await self._emit(AlertType.ERROR, "Compra manual falhou", f"{symbol} fora da whitelist.")
            return
        now = time.time()
        try:
            equity = await asyncio.to_thread(self._equity_usd)
        except TwakError as exc:
            await self._emit(AlertType.ERROR, "Compra manual falhou", str(exc))
            return
        self._risk.update_equity(equity)
        stable = self._stable_usd()  # USDC real disponível (não a equity total)
        gate = self._risk.can_open_position(current_equity_usd=equity, available_stable_usd=stable,
                                            open_positions=len(self.positions), now_ts=now)
        if not gate.allowed:
            await self._emit(AlertType.ERROR, "Compra manual bloqueada", gate.detail)
            return
        size = self._risk.position_size_usd(equity, stable, override_pct=size_pct)
        if size <= 0:
            await self._emit(AlertType.ERROR, "Compra manual bloqueada",
                             f"USDC insuficiente (disponível ${stable:.2f}, mínimo ${self._cfg.min_position_usd:.2f}).")
            return
        val = await asyncio.to_thread(
            self._validator.validate, symbol=symbol, token_address=addr, amount_usd=size)
        if not val.ok:
            await self._emit(AlertType.REJECTED, f"Compra manual barrada: {symbol}", val.detail)
            return
        tag = f"compra manual {size_pct:.0f}%" if size_pct is not None else "compra manual"
        await self._open(symbol, addr, size, f"{tag} (validação)", now)

    async def sell_position(self, symbol: str) -> bool:
        """Venda manual de UMA posição aberta (a mercado), pelo dono via Telegram.
        Não trava o agente (diferente do /panic) — ele segue operando."""
        symbol = symbol.upper()
        pos = next((p for p in self.positions if p.symbol.upper() == symbol), None)
        if not pos:
            await self._emit(AlertType.ERROR, "Venda manual", f"Sem posição aberta de {symbol}.")
            return False
        try:
            price = await asyncio.to_thread(self._validator.onchain_price_usd, pos.token_address)
        except Exception:  # noqa: BLE001
            price = None
        await self._sell(pos, reason="SELL_MANUAL", exit_price=price)
        return True

    async def sell_all_positions(self) -> int:
        """Vende TODAS as posições abertas a mercado (sem travar o agente). Retorna a contagem."""
        n = 0
        for pos in list(self.positions):
            if await self.sell_position(pos.symbol):
                n += 1
        return n

    async def register_competition(self) -> dict:
        """Registra a carteira do agente na competição (twak compete register). On-chain,
        roda UMA vez antes da semana ao vivo. Trades só contam após o registro."""
        return await asyncio.to_thread(self._executor.register_competition, self._password)

    async def competition_status(self) -> dict:
        """Consulta o status do registro na competição (twak compete status)."""
        return await asyncio.to_thread(self._executor.competition_status, self._password)

    # ── loops ────────────────────────────────────────────────────────────────
    async def _scan_loop(self) -> None:
        interval = int(self._cfg.loop["scan_interval_seconds"])
        while self.state in (AgentState.SCANNING, AgentState.IN_POSITION):
            try:
                await self.run_cycle(time.time())
            except Exception as exc:  # noqa: BLE001
                self._log.exception("Erro no scan: %s", exc)
                await self._emit(AlertType.ERROR, "Erro no ciclo de scan", str(exc))
            await asyncio.sleep(interval)

    async def _monitor_loop(self) -> None:
        interval = int(self._cfg.loop["position_monitor_interval_seconds"])
        while self.state in (AgentState.SCANNING, AgentState.IN_POSITION):
            try:
                await self.check_positions()
            except Exception as exc:  # noqa: BLE001
                self._log.exception("Erro no monitor: %s", exc)
            await asyncio.sleep(interval)

    # ── um ciclo de scan (testável) ──────────────────────────────────────────
    async def run_cycle(self, now: float) -> None:
        if self.state not in (AgentState.SCANNING, AgentState.IN_POSITION):
            return
        try:
            equity, reliable = await asyncio.to_thread(self._equity_usd_checked)
        except TwakError as exc:
            await self._emit(AlertType.ERROR, "Equity indisponível", str(exc))
            return

        # PROTEÇÃO ANTI FALSO-DISJUNTOR: se a leitura não é íntegra (RPC limitado / posição
        # sem rota de preço), a equity vem deflacionada. NÃO disparamos o disjuntor nem
        # operamos neste ciclo — um soluço de preço não pode liquidar a carteira por engano.
        # O pico/âncora também não se atualizam com dado ruim. Tenta de novo no próximo ciclo.
        if not reliable:
            self._log.warning(
                "Leitura de carteira NÃO-confiável ($%.2f) — ciclo pulado (RPC/preço). "
                "Disjuntor e trades suspensos até a leitura normalizar.", equity)
            return

        self._risk.update_equity(equity, now)
        self._last_equity = equity  # cache p/ dashboard
        await self._reconcile_positions()  # sincroniza tracking↔on-chain (limpa poeira de teste)
        stable = self._stable_usd()  # USDC real disponível p/ comprar (não a equity total)
        if self._risk.circuit_breaker_tripped(equity):
            await self.panic(f"Drawdown {self._risk.current_drawdown_pct(equity):.1f}% atingiu o gatilho.")
            return
        if self._risk.daily_loss_tripped(equity):
            await self.panic(f"Perda diária {self._risk.daily_drawdown_pct(equity):.1f}% atingiu o limite do dia.")
            return

        if self._risk.needs_heartbeat(now):
            await self._heartbeat(now)

        await self._publish_risk_state()  # disjuntor on-chain: atesta o estado ~1x/hora (best-effort)

        # ECONOMIA: 1 chamada CMC p/ TODAS as cotações (batch) + 1 global.
        global_metrics = await self._analyzer.gather_global()
        quotes = await self._analyzer.gather_quotes(self.token_focus)
        if not quotes:
            await self._emit(AlertType.DATA_ERROR, "Sem dados de mercado",
                             "Falha ao obter cotações da CMC (REST). Verifique a API key / cota.")
            return

        # SKILL Radar de Atenção: junta os maiores ganhos do MERCADO que estão na
        # whitelist elegível (tradáveis) ao universo — pega surtos fora da cesta fixa.
        try:
            movers = await self._analyzer.gather_movers(set(self._token_addr.keys()), top_n=8)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Radar de atenção indisponível: %s", exc)
            movers = {}
        for sym, m in movers.items():
            quotes.setdefault(sym, m)
        universe = list(dict.fromkeys(list(self.token_focus) + list(movers.keys())))

        # MACRO PRIMEIRO (o seletor de estratégia precisa do F&G p/ rotear pânico→DCA).
        macro = await self._analyzer.gather_macro()
        btc24, fng, funding = macro.get("btc_24h"), macro.get("fng"), macro.get("funding")
        # Publica o mercado REAL p/ a demo do site reusar (mesmo processo) — custo zero.
        market_cache.put(dict(quotes), btc24, fng)
        systemic = btc24 is not None and btc24 <= -5.0
        # DEPEG GUARD: se a stable de trade (USDC) desviar de $1, o "caixa seguro" não está
        # seguro → bloqueia TODAS as entradas e alerta o dono (não liquida).
        depeg = self._stable_depeg_bps(macro)
        depeg_block = depeg is not None and depeg >= self._cfg.stable_depeg_bps > 0
        if depeg_block:
            await self._maybe_alert_depeg(depeg)
        # SKILL Adaptação por regime: preço (BTC) + SENTIMENTO (medo/ganância) + ALAVANCAGEM.
        regime_label, cut_adjust = market_regime(btc24, fng, funding)
        # F&G #1 (ganância extrema): aperta os exits das posições abertas (trava lucro no topo).
        self._extreme_greed = fng is not None and fng >= 78
        # F&G #2 (DIREÇÃO, não só nível): EMA suave (~2,7h) lê se o sentimento está MELHORANDO
        # (medo aliviando → repique começando) ou PIORANDO (ganância esfriando → topo virando).
        # O sinal mais forte é a virada, não o valor estático.
        if fng is not None:
            self._fng_ema = float(fng) if self._fng_ema is None else 0.97 * self._fng_ema + 0.03 * fng
            trend = fng - self._fng_ema
            if trend >= 4 and fng <= 55:          # medo aliviando / recuperação começando
                cut_adjust -= 2
                regime_label += "↑"
            elif trend <= -4 and fng >= 50:        # ganância esfriando / topo virando
                cut_adjust += 2
                regime_label += "↓"

        # Pré-score (só p/ ranquear/logar) + SELEÇÃO DE ESTRATÉGIA por token (o gatilho REAL).
        prescores = [(s, momentum_prescore(quotes.get(s))) for s in universe if quotes.get(s)]
        ranked = [s for s, _ in sorted(prescores, key=lambda x: -x[1])]  # p/ rotação/log
        # PLAYBOOK: cada token é roteado p/ a estratégia cujo gatilho determinístico dispara
        # (momentum / mean-reversion / dca), conforme o regime + sinais. Em pânico, só DCA.
        fired: list = []  # [(spec, symbol, strength)]
        for s in universe:
            mq = quotes.get(s)
            if not mq or self._exec_cooldown.get(s, 0.0) > now:
                continue
            spec = select_strategy(fng, mq)
            if spec:
                fired.append((spec, s, setup_strength(spec, mq)))
        fired.sort(key=lambda x: -x[2])
        TOP_K = 3
        STRONG = 78  # setup claramente forte: para de gastar chamadas e já entra

        opened = False
        claude_calls = 0
        buys: list = []   # [(verdict, symbol, addr, spec)] de TODOS que deram BUY
        top_eval = ""
        gate = self._risk.can_open_position(
            current_equity_usd=equity, available_stable_usd=stable,
            open_positions=len(self.positions), now_ts=now,
        )
        # DCA é a estratégia de PÂNICO → ignora o gate de BTC despencando (foi FEITA pra isso).
        # Depeg bloqueia tudo; disjuntor/cap/max-posições (gate) continuam valendo sempre.
        active_is_dca = bool(fired) and fired[0][0].key == "dca"
        block = depeg_block or (systemic and not active_is_dca)
        gate_note = ""
        if depeg_block:
            gate_note = f" · Gate DEPEG: stable desviou {depeg/100:.2f}% (risco sistêmico)"
        elif block:
            gate_note = f" · Gate MACRO: BTC {btc24:+.1f}%/24h (risco sistêmico)"
        elif not gate.allowed:
            gate_note = f" · Gate BLOQUEOU: {gate.detail}"
        memory = self._performance_digest()  # SKILL Memória: histórico p/ o cérebro calibrar
        if gate.allowed and not block and fired:
            best_score = -1
            for spec, symbol, _strength in fired[:TOP_K]:
                addr = self._addr(symbol)
                if not addr:
                    continue
                # Mean-rev/DCA: o ajuste DEFENSIVO de regime NÃO se aplica (a estratégia já É a
                # resposta ao regime). O cérebro CONFIRMA o setup no frame da estratégia ativa.
                ca = cut_adjust if spec.key == "momentum" else 0
                verdict = await self._analyzer.evaluate(
                    symbol, raw_metrics={**global_metrics, **quotes[symbol]},
                    memory=memory, cut_adjust=ca, strategy=spec.key)
                claude_calls += 1
                if verdict.confidence_score > best_score:
                    best_score = verdict.confidence_score
                    top_eval = f"{symbol} {verdict.confidence_score}{'✓' if verdict.is_buy else ''} [{spec.key}]"
                if verdict.is_buy:
                    buys.append((verdict, symbol, addr, spec))
                if verdict.is_buy and verdict.confidence_score >= STRONG:
                    break

            for verdict, symbol, addr, spec in sorted(buys, key=lambda b: -b[0].confidence_score):
                ch24 = float(quotes[symbol].get("percent_change_24h") or 0.0)
                # Anti-topo + amortecimento de esticamento SÓ p/ MOMENTUM. Mean-rev e DCA
                # compram QUEDAS de propósito — penalizá-las aqui mataria a estratégia.
                if spec.key == "momentum" and ch24 > self._cfg.max_entry_24h_pct:
                    await self._emit(AlertType.REJECTED, f"Entrada barrada (esticado): {symbol}",
                                     f"24h {ch24:+.1f}% > {self._cfg.max_entry_24h_pct:.0f}% — risco de topo.")
                    continue
                conv_pct = conviction_size_pct(self.position_size_pct, verdict.confidence_score,
                                               max_pct=self._cfg.max_position_pct)
                if spec.key == "momentum":
                    conv_pct *= overextension_factor(ch24)
                size = self._risk.position_size_usd(equity, stable, override_pct=conv_pct)
                val = await asyncio.to_thread(
                    self._validator.validate, symbol=symbol, token_address=addr, amount_usd=size,
                    cmc_price_usd=quotes[symbol].get("price_usd"),  # ativa checagem de oráculo
                )
                if not val.ok:
                    await self._emit(AlertType.REJECTED, f"Trade barrado: {symbol}",
                                     val.detail, reason=val.reason.value if val.reason else "")
                    continue
                # ERC-8004: SELA o raciocínio on-chain ANTES de executar (não-bloqueante).
                asyncio.create_task(self._commit_prediction(symbol, verdict, ch24, now))
                # Abre com os parâmetros DA ESTRATÉGIA (SL/TP/trailing/time-stop próprios).
                if await self._open(symbol, addr, size, f"[{spec.label}] {verdict.rationale}", now,
                                    stop_pct=spec.stop_pct, tp_pct=spec.take_profit_pct,
                                    trailing_trigger_pct=spec.trailing_trigger_pct,
                                    trailing_pct=spec.trailing_pct, time_stop_min=spec.time_stop_min,
                                    time_stop_band_pct=spec.time_stop_band_pct,
                                    strategy=spec.key, regime=verdict.regime):
                    opened = True
                    break
                # Execução falhou (ex.: revert por liquidez): cooldown e tenta o próximo.
                self._exec_cooldown[symbol] = now + 7200  # 2h fora do radar
                self._log.info("%s em cooldown de execução (2h) após falha de swap.", symbol)

        # SKILL Rotação por oportunidade: 100% alocado, mas surgiu algo MUITO melhor? Gira
        # do holding mais FRACO (nunca de um vencedor em corrida) p/ liberar capital.
        rot_note = ""
        if (not opened and not block and self.positions
                and not gate.allowed and "insuficiente" in (gate.detail or "").lower()):
            rot_note = await self._maybe_rotate(equity, quotes, global_metrics, ranked, now)

        if opened:
            return  # TRADE_OPENED já notificou
        top = sorted(prescores, key=lambda x: -x[1])[:3]
        top_str = " · ".join(f"{s} {sc}" for s, sc in top) or "—"
        melhor = f" · Melhor avaliado: {top_eval}" if top_eval else ""
        radar = f" · Radar: +{len(movers)} movers" if movers else ""
        reg = f" · Regime: {regime_label}({cut_adjust:+d})" if regime_label != "NEUTRO" else ""
        detail = (f"Analisei {len(prescores)} tokens (momentum){radar}{reg}. Top: {top_str}. "
                  f"Candidatos p/ IA: {len(fired)} · Claude: {claude_calls} chamada(s)."
                  f"{melhor}{gate_note}{rot_note} Sem entrada.")
        self._log.info("CICLO | %s", detail)  # visibilidade no log do servidor
        await self._emit(AlertType.SCAN, "Ciclo concluído", detail)
        self._save()

    async def _maybe_rotate(self, equity: float, quotes: dict, global_metrics: dict,
                            ranked: list, now: float) -> str:
        """SKILL Rotação: 100% alocado, mas surgiu um candidato de ALTA convicção? Vende o
        holding mais FRACO (e que NÃO é vencedor em corrida) p/ liberar capital. A compra
        entra no próximo ciclo (o cooldown da venda evita churn). Retorna nota p/ o log."""
        ROTATION_CUT = 72
        held = {p.symbol.upper() for p in self.positions}
        cand = next((s for s in ranked if self._addr(s) and s.upper() not in held), None)
        if not cand:
            return ""
        if float(quotes.get(cand, {}).get("percent_change_24h") or 0.0) > self._cfg.max_entry_24h_pct:
            return ""  # trava anti-topo: não rotaciona p/ um token esticado
        verdict = await self._analyzer.evaluate(
            cand, raw_metrics={**global_metrics, **quotes[cand]}, memory=self._performance_digest())
        if not verdict.is_buy or verdict.confidence_score < ROTATION_CUT:
            return ""
        # holding mais fraco que NÃO é vencedor em corrida (preserva os ganhadores!)
        weakest = None
        weakest_pre, weakest_px = 999, 0.0
        for pos in self.positions:
            try:
                px = await asyncio.to_thread(self._validator.onchain_price_usd, pos.token_address)
            except Exception:  # noqa: BLE001
                continue
            pnl = ((px - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price else 0.0
            if pnl >= 5.0 or pos.trailing_active:  # vencedor em corrida → NUNCA rotaciona
                continue
            pre = momentum_prescore(quotes.get(pos.symbol)) if quotes.get(pos.symbol) else 0
            if pre < weakest_pre:
                weakest_pre, weakest, weakest_px = pre, pos, px
        if weakest is None or weakest_pre >= 15:  # só rotaciona se o holding é claramente fraco
            return ""
        self._log.info("ROTAÇÃO | vendo %s (fraco pre=%d) → liberar capital p/ %s (score %d)",
                       weakest.symbol, weakest_pre, cand, verdict.confidence_score)
        await self._emit(AlertType.SCAN, "🔁 Rotação de capital",
                         f"Saindo de {weakest.symbol} (sinal fraco) p/ perseguir {cand} "
                         f"(score {verdict.confidence_score}). Compra no próximo ciclo.")
        await self._sell(weakest, reason="SELL_ROTACAO", exit_price=weakest_px)
        return f" · 🔁 Rotação: {weakest.symbol}→{cand}({verdict.confidence_score})"

    async def _open(self, symbol: str, addr: str, size_usd: float, rationale: str, now: float,
                    *, stop_pct: float = 0.0, tp_pct: float = 0.0, regime: str = "",
                    strategy: str = "", trailing_trigger_pct: float = 0.0, trailing_pct: float = 0.0,
                    time_stop_min: float = 0.0, time_stop_band_pct: float = 0.0) -> bool:
        """Abre a posição (swap real). Retorna True se abriu, False se a execução falhou.

        Com `strategy`, usa os parâmetros DA ESTRATÉGIA literalmente (stop_pct=0 = SEM SL,
        ex.: DCA). Sem strategy (compra manual), stop/tp caem no fixo do config."""
        def _do_buy():
            return self._executor.buy(to_token=addr, amount_usd=size_usd, password=self._password)
        with self._risk.trade_lock:
            res = await asyncio.to_thread(_do_buy)
        # 1ª compra de um token novo exige approval do gasto. O twak envia a aprovacao,
        # mas o swap pode reverter ANTES dela minerar ("Approval was sent... Check allowance
        # before retrying"). Detecta isso e re-tenta UMA vez apos a aprovacao confirmar
        # (~20s, blocos BSC ~3s). O sleep fica FORA do lock p/ nao travar o monitor de stops.
        if not res.ok and res.error and any(
                k in res.error.lower() for k in ("allowance", "approval was sent")):
            self._log.info("Approval enviado p/ %s; aguardando ~20s p/ minerar e re-tentando swap.", symbol)
            await asyncio.sleep(20)
            with self._risk.trade_lock:
                res = await asyncio.to_thread(_do_buy)
        if not res.ok:
            await self._emit(AlertType.ERROR, f"Compra falhou: {symbol}", res.error)
            return False
        entry = res.entry_price or await asyncio.to_thread(self._validator.onchain_price_usd, addr)
        # SL/TP efetivo. Com estratégia: literal (stop=0 → SEM SL, ex.: DCA). Manual: fixo do config.
        if strategy:
            eff_stop, eff_tp = stop_pct, tp_pct
        else:
            eff_stop = stop_pct or self.stop_loss_pct
            eff_tp = tp_pct or self.take_profit_pct
        stop_price = entry * (1.0 - eff_stop / 100.0) if eff_stop > 0 else 0.0  # 0 = sem SL (disjuntor global cobre)
        tp_price = entry * (1.0 + eff_tp / 100.0) if eff_tp > 0 else 0.0
        pos = Position(symbol=symbol, token_address=addr, entry_price=entry, amount_usd=size_usd,
                       qty=res.qty or 0.0, stop_loss_price=stop_price,
                       stop_loss_pct=eff_stop, take_profit_pct=eff_tp,
                       opened_at=now, tx_hash=res.tx_hash, regime=regime, strategy=strategy,
                       trailing_trigger_pct=trailing_trigger_pct, trailing_pct=trailing_pct,
                       time_stop_min=time_stop_min, time_stop_band_pct=time_stop_band_pct)
        self.positions.append(pos)
        self.state = AgentState.IN_POSITION
        self._risk.record_trade(now)
        vol_note = f" | vol {pos.stop_loss_pct:.1f}/{pos.take_profit_pct:.1f}%" if stop_pct else ""
        self._log.info("TRADE ABERTO | %s $%.2f @ %.8g%s | tx=%s",
                       symbol, size_usd, entry, vol_note, res.tx_hash)
        append_trade({"type": "open", "symbol": symbol, "amount_usd": size_usd,
                      "entry_price": entry, "tx": res.tx_hash, "ts": now, "regime": regime,
                      "strategy": strategy})
        await self._emit(AlertType.TRADE_OPENED, f"Posição aberta: {symbol}",
                         rationale, amount_usd=size_usd, entry=entry,
                         stop=stop_price, take_profit=tp_price,
                         stop_pct=eff_stop, take_profit_pct=eff_tp,
                         tx=res.tx_hash)
        self._save()
        return True

    # ── monitor de posições (stop / trailing) ────────────────────────────────
    async def check_positions(self) -> None:
        for pos in list(self.positions):
            try:
                price = await asyncio.to_thread(self._validator.onchain_price_usd, pos.token_address)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("preço indisponível %s: %s", pos.symbol, exc)
                continue
            signal = self._risk.evaluate_position(pos, price, tighten=self._extreme_greed)
            if signal != ExitSignal.HOLD:
                await self._sell(pos, reason=signal.value, exit_price=price)
            else:
                # SKILL Saída Inteligente: se o mecânico diz HOLD, o cérebro reavalia a
                # tese (a cada ~5min) e pode sair antes — proteger lucro / cortar reversão.
                await self._smart_exit_check(pos, price)
        if not self.positions and self.state == AgentState.IN_POSITION:
            self.state = AgentState.SCANNING

    async def _smart_exit_check(self, pos: Position, price: float) -> bool:
        """Reavaliação qualitativa da posição pelo cérebro (throttled). Sai se a tese
        quebrou (momentum virou / volume secou / esticou), independente do stop."""
        now = time.time()
        if now - self._exit_checks.get(pos.symbol, 0.0) < 300:  # 1 reavaliação / 5min / posição
            return False
        self._exit_checks[pos.symbol] = now
        pnl = ((price - pos.entry_price) / pos.entry_price * 100.0) if pos.entry_price else 0.0
        try:
            decision = await self._analyzer.evaluate_exit(
                pos.symbol, pnl_pct=pnl, held_min=(now - pos.opened_at) / 60.0)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Saída inteligente indisponível (%s): %s", pos.symbol, exc)
            return False
        if decision.should_exit:
            self._log.info("SAÍDA INTELIGENTE | %s (PnL %+.2f%%): %s", pos.symbol, pnl, decision.reason)
            await self._sell(pos, reason="SELL_BRAIN", exit_price=price)
            return True
        return False

    async def _sell(self, pos: Position, *, reason: str, exit_price: float | None = None) -> None:
        # Vende o saldo REAL on-chain (não o qty rastreado, que pode estar defasado por
        # dust/fee-on-transfer) com 0,1% de folga — evita "transfer amount exceeds balance".
        try:
            raw = await asyncio.to_thread(self._validator._token_balance, pos.token_address, self.agent_address)
            dec = await asyncio.to_thread(self._validator._decimals, pos.token_address)
            real_qty = raw / (10 ** dec)
        except Exception:  # noqa: BLE001
            real_qty = pos.qty
        px = exit_price or pos.entry_price or 0.0
        if real_qty <= 0 or real_qty * px < 0.10:
            # Só restou poeira → a posição já saiu de fato; destrava o tracking.
            if pos in self.positions:
                self.positions.remove(pos)
            self._save()
            self._log.info("%s já liquidada on-chain (poeira); removida do tracking.", pos.symbol)
            await self._emit(AlertType.TRADE_CLOSED, f"Posição encerrada: {pos.symbol}",
                             "Já estava vendida (só restava poeira on-chain).", reason=reason,
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
        # 1ª venda de um token exige approval (token→router); o swap pode reverter antes
        # dela minerar. Re-tenta UMA vez após ~20s (igual à compra). Sleep fora do lock.
        if not res.ok and res.error and any(
                k in res.error.lower() for k in ("allowance", "approval was sent")):
            self._log.info("Approval de venda enviado p/ %s; aguardando ~20s e re-tentando.", pos.symbol)
            await asyncio.sleep(20)
            with self._risk.trade_lock:
                res = await asyncio.to_thread(_do_sell)
        if not res.ok:
            # NÃO remove a posição: o token segue na carteira → continua gerenciado e
            # vendível (evita virar holding órfão sem stop). Só avisa a falha.
            self._log.warning("Venda de %s falhou: %s (posição mantida).", pos.symbol, res.error)
            await self._emit(AlertType.ERROR, f"Venda falhou: {pos.symbol}",
                             (res.error or "") + " — posição mantida; tente de novo.")
            return
        if pos in self.positions:
            self.positions.remove(pos)
        self._risk.record_trade(time.time())
        self._log.info("TRADE FECHADO | %s motivo=%s | tx=%s", pos.symbol, reason, res.tx_hash)
        pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price * 100.0) if exit_price else None
        append_trade({"type": "close", "symbol": pos.symbol, "reason": reason,
                      "pnl_pct": pnl_pct, "tx": res.tx_hash, "ts": time.time(),
                      "regime": pos.regime})
        await self._emit(AlertType.TRADE_CLOSED, f"Posição encerrada: {pos.symbol}",
                         "", reason=reason, pnl_pct=pnl_pct,
                         entry=pos.entry_price, exit=exit_price, amount_usd=pos.amount_usd,
                         tx=res.tx_hash)
        self._save()
        await self._publish_reputation()  # SKILL: atualiza reputação on-chain (best-effort)

    def _performance_digest(self) -> str:
        """SKILL Memória: resumo do PRÓPRIO histórico p/ o cérebro se calibrar (aprende com
        o que fez). Win-rate, PnL médio, últimos resultados e tokens que sangraram."""
        closes = [t for t in load_trades() if t.get("type") == "close" and t.get("pnl_pct") is not None]
        if len(closes) < 3:
            return ""  # histórico curto → não enviesa
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
        note = (f"SEU HISTORICO ({len(recent)} trades): {wr:.0f}% de acerto, PnL medio "
                f"{avg:+.1f}%, ultimos 5: {last5}.")
        # WIN-RATE POR REGIME: mostra ao cérebro ONDE ele ganha e onde sangra (ex.: chop).
        by_reg: dict[str, list] = defaultdict(list)
        for t in recent:
            r = (t.get("regime") or "").strip().lower()
            if r:
                by_reg[r].append(t["pnl_pct"])
        reg_parts = [f"{r} {sum(1 for x in v if x>0)/len(v)*100:.0f}%% ({len(v)})"
                     for r, v in by_reg.items() if len(v) >= 2]
        if reg_parts:
            note += " Por REGIME: " + ", ".join(reg_parts).replace("%%", "%") + "."
            chop = by_reg.get("choppy", [])
            if len(chop) >= 3 and sum(1 for x in chop if x > 0) / len(chop) < 0.4:
                note += " Voce PERDE no choppy — seja bem mais seletivo (ou ESPERE) no lateral."
        if sangra:
            note += (" Tokens que te sangraram: "
                     + ", ".join(f"{s}({a:+.0f}%)" for s, a in sangra)
                     + " — seja MAIS seletivo neles.")
        note += " Use isso p/ calibrar conviccao: se vem perdendo, suba a barra; se acertando, confie."
        return note

    async def _commit_prediction(self, symbol: str, verdict, ch24: float, now: float) -> None:
        """Sela o raciocínio ON-CHAIN antes da execução (anti-fabricação). NÃO-BLOQUEANTE
        e best-effort: roda em paralelo ao swap; se falhar, o trade acontece igual."""
        pred = {"symbol": symbol, "score": verdict.confidence_score,
                "volatility": verdict.volatility, "ch24": round(ch24, 1),
                "rationale": verdict.rationale[:220], "ts": int(now)}
        try:
            res = await asyncio.to_thread(identity.commit_prediction, pred)
            if res:
                self._log.info("PRÉ-COMMIT on-chain | %s hash=%s tx=%s",
                               symbol, res["hash"][:14], res.get("tx"))
                await self._emit(AlertType.STARTED, "🔒 Raciocínio selado on-chain (pré-execução)",
                                 f"{symbol}: a tese foi gravada na BNB Chain ANTES do trade — "
                                 "prova verificável, não dá pra inventar depois.", tx=res.get("tx"))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Pré-commit on-chain falhou (ignorado): %s", exc)

    async def _publish_reputation(self) -> None:
        """SKILL Reputação on-chain: publica o histórico (trades/win-rate/PnL) como
        metadata ERC-8004 verificável. Throttled a ~1x/hora; best-effort (gás/rede)."""
        now = time.time()
        if now - self._last_rep_publish < 3600:
            return
        closes = [t for t in load_trades() if t.get("type") == "close" and t.get("pnl_pct") is not None]
        if not closes:
            return
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
                self._last_rep_publish = now
                await self._emit(AlertType.STARTED, "🏅 Reputação on-chain atualizada",
                                 f"{stats['trades']} trades · {stats['win_rate']:.0f}% win · "
                                 f"PnL acum {stats['total_pnl_pct']:+.2f}%",
                                 tx=res.get("transactionHash"))
                self._log.info("Reputação on-chain publicada: %s (tx=%s)", stats, res.get("transactionHash"))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Publicação de reputação falhou (ignorado): %s", exc)

    async def _publish_risk_state(self, *, halted_event: bool = False) -> None:
        """DISJUNTOR ON-CHAIN: atesta o estado do circuit breaker no ERC-8004 (chave
        'risk_state'). Throttled ~1x/hora, MAS sempre que o killswitch dispara
        (halted_event) — aí fica a prova on-chain de que a trava agiu. Best-effort."""
        now = time.time()
        if not halted_event and now - self._last_risk_publish < 3600:
            return
        equity = self._last_equity or 0.0
        if equity <= 0 and not halted_event:
            return
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
                self._last_risk_publish = now
                self._log.info("Disjuntor atestado on-chain: %s (tx=%s)", state, res.get("transactionHash"))
                if halted_event:
                    await self._emit(AlertType.CIRCUIT_BREAKER, "🔒 Disjuntor selado on-chain",
                                     f"Drawdown {state['drawdown_bps']/100:.1f}% — prova ERC-8004",
                                     tx=res.get("transactionHash"))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Atestação on-chain do disjuntor falhou (ignorado): %s", exc)

    # ── heartbeat (mínimo de trades) ─────────────────────────────────────────
    async def _heartbeat(self, now: float) -> None:
        """Swap de manutenção ~$1 USDT→USDC para cumprir o mínimo de trades/dia.

        Risco de mercado nulo (stable↔stable). Real no modo ao vivo; simulado no paper.
        """
        # Heartbeat = swap da stable base para a OUTRA stable (risco de mercado ~zero).
        base_sym = self._cfg.dev_safety["base_stable_symbol"].upper()
        target = (self._cfg.network.get("usdt_bsc_address") if base_sym == "USDC"
                  else self._cfg.network.get("usdc_bsc_address"))
        if target:
            try:
                res = await asyncio.to_thread(
                    self._executor.buy, to_token=target, amount_usd=1.05, password=self._password)
                detail = (f"Swap manutenção stable↔stable (~$1): "
                          f"{'ok ' + (res.tx_hash or '') if res.ok else 'falhou: ' + res.error}")
            except Exception as exc:  # noqa: BLE001
                detail = f"Heartbeat falhou: {exc}"
        else:
            detail = "Endereço da stable alvo não configurado."
        self._risk.record_trade(now)
        await self._emit(AlertType.HEARTBEAT, "Heartbeat trade", detail)
        self._save()
