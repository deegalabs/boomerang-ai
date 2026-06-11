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

from boomerang.brain.cmc_analyzer import AttentionAnalyzer, momentum_prescore, passes_prefilter
from boomerang.config import Config
from boomerang.identity import bnb_agent as identity
from boomerang.ipc import Alert, AlertBus, AlertType
from boomerang.persistence import append_trade, load_state, load_trades, save_state
from boomerang.risk import RiskEngine
from boomerang.risk.risk_engine import ExitSignal
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
    def _equity_usd(self) -> float:
        """Patrimônio total em USD lido ON-CHAIN (conta BNB + stables + TODOS os
        tokens, inclusive a posição aberta). É a base correta do drawdown/circuit
        breaker. Cai no portfolio do TWAK só se a leitura on-chain falhar.

        Bugfix (8 jun): o portfolio do TWAK não somava o token da posição → equity
        caía ao abrir trade e inflava o drawdown (disparava o disjuntor por engano).
        """
        addr = self.agent_address
        if addr:
            try:
                bd = self._validator.wallet_breakdown(addr)
                total = float(bd.get("total_usd") or 0.0)
                if total > 0:
                    self._last_holdings = bd.get("holdings", [])
                    return total
            except Exception as exc:  # noqa: BLE001
                self._log.warning("Equity on-chain indisponível (%s); usando TWAK.", exc)
        return self._executor.portfolio_usd(self._password)

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
            await self._emit(AlertType.ERROR, "Agente travado", "Circuit breaker ativo. Reinicie a sessão.")
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
            equity = await asyncio.to_thread(self._equity_usd)
        except TwakError as exc:
            await self._emit(AlertType.ERROR, "Equity indisponível", str(exc))
            return

        self._risk.update_equity(equity)
        self._last_equity = equity  # cache p/ dashboard
        await self._reconcile_positions()  # sincroniza tracking↔on-chain (limpa poeira de teste)
        stable = self._stable_usd()  # USDC real disponível p/ comprar (não a equity total)
        if self._risk.circuit_breaker_tripped(equity):
            await self.panic(f"Drawdown {self._risk.current_drawdown_pct(equity):.1f}% atingiu o gatilho.")
            return

        if self._risk.needs_heartbeat(now):
            await self._heartbeat(now)

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

        # Pré-score determinístico (de graça) para ranquear e FILTRAR quem vai ao LLM.
        prescores = [(s, momentum_prescore(quotes.get(s))) for s in universe if quotes.get(s)]
        candidates = [s for s, _ in prescores
                      if passes_prefilter(quotes.get(s), self._cfg.prefilter_min_vol_change)
                      and self._exec_cooldown.get(s, 0.0) <= now]  # pula intradáveis recentes
        # Postura OPORTUNISTA: ordena por momentum e avalia os TOP-K mais promissores,
        # escolhendo o MELHOR veredito (maior score) — não o primeiro qualquer. Assim o
        # agente opera a melhor oportunidade RELATIVA do ciclo, inclusive em mercado calmo.
        ranked = sorted(candidates, key=lambda s: momentum_prescore(quotes.get(s)), reverse=True)
        TOP_K = 3
        STRONG = 78  # setup claramente forte: para de gastar chamadas e já entra

        opened = False
        claude_calls = 0
        buys: list = []   # [(verdict, symbol, addr)] de TODOS que deram BUY, p/ fallback
        top_eval = ""     # melhor score avaliado no ciclo (p/ visibilidade no log)
        # gate (cooldown/max posições/drawdown/stable) não depende do símbolo: 1 checagem.
        gate = self._risk.can_open_position(
            current_equity_usd=equity, available_stable_usd=stable,
            open_positions=len(self.positions), now_ts=now,
        )
        if gate.allowed:
            best_score = -1
            for symbol in ranked[:TOP_K]:
                addr = self._addr(symbol)
                if not addr:
                    continue
                # Só AQUI gastamos uma chamada (paga) ao Claude — apenas nos melhores candidatos.
                verdict = await self._analyzer.evaluate(
                    symbol, raw_metrics={**global_metrics, **quotes[symbol]})
                claude_calls += 1
                if verdict.confidence_score > best_score:
                    best_score = verdict.confidence_score
                    top_eval = f"{symbol} {verdict.confidence_score}{'✓' if verdict.is_buy else ''}"
                if verdict.is_buy:
                    buys.append((verdict, symbol, addr))
                if verdict.is_buy and verdict.confidence_score >= STRONG:
                    break  # já achamos um setup forte; não precisa avaliar mais

            # Tenta do MELHOR pro pior: se Filtro 2 barra OU a execução reverte, cai no próximo.
            for verdict, symbol, addr in sorted(buys, key=lambda b: -b[0].confidence_score):
                size = self._risk.position_size_usd(equity, stable)
                val = await asyncio.to_thread(
                    self._validator.validate, symbol=symbol, token_address=addr, amount_usd=size,
                    cmc_price_usd=quotes[symbol].get("price_usd"),  # ativa checagem de oráculo
                )
                if not val.ok:
                    await self._emit(AlertType.REJECTED, f"Trade barrado: {symbol}",
                                     val.detail, reason=val.reason.value if val.reason else "")
                    continue  # tenta o próximo melhor BUY
                if await self._open(symbol, addr, size, verdict.rationale, now):
                    opened = True
                    break
                # Execução falhou (ex.: revert por liquidez): cooldown e tenta o próximo.
                self._exec_cooldown[symbol] = now + 7200  # 2h fora do radar
                self._log.info("%s em cooldown de execução (2h) após falha de swap.", symbol)

        if opened:
            return  # TRADE_OPENED já notificou
        top = sorted(prescores, key=lambda x: -x[1])[:3]
        top_str = " · ".join(f"{s} {sc}" for s, sc in top) or "—"
        melhor = f" · Melhor avaliado: {top_eval}" if top_eval else ""
        radar = f" · Radar: +{len(movers)} movers" if movers else ""
        detail = (f"Analisei {len(prescores)} tokens (momentum){radar}. Top: {top_str}. "
                  f"Candidatos p/ IA: {len(candidates)} · Claude: {claude_calls} chamada(s).{melhor} Sem entrada.")
        self._log.info("CICLO | %s", detail)  # visibilidade no log do servidor
        await self._emit(AlertType.SCAN, "Ciclo concluído", detail)
        self._save()

    async def _open(self, symbol: str, addr: str, size_usd: float, rationale: str, now: float) -> bool:
        """Abre a posição (swap real). Retorna True se abriu, False se a execução falhou."""
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
        pos = Position(symbol=symbol, token_address=addr, entry_price=entry, amount_usd=size_usd,
                       qty=res.qty or 0.0, stop_loss_price=self._risk.initial_stop_price(entry),
                       opened_at=now, tx_hash=res.tx_hash)
        self.positions.append(pos)
        self.state = AgentState.IN_POSITION
        self._risk.record_trade(now)
        self._log.info("TRADE ABERTO | %s $%.2f @ %.8g | tx=%s", symbol, size_usd, entry, res.tx_hash)
        append_trade({"type": "open", "symbol": symbol, "amount_usd": size_usd,
                      "entry_price": entry, "tx": res.tx_hash, "ts": now})
        await self._emit(AlertType.TRADE_OPENED, f"Posição aberta: {symbol}",
                         rationale, amount_usd=size_usd, entry=entry,
                         stop=pos.stop_loss_price, take_profit=self._risk.take_profit_price(entry),
                         stop_pct=self.stop_loss_pct, take_profit_pct=self.take_profit_pct,
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
            signal = self._risk.evaluate_position(pos, price)
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
                      "pnl_pct": pnl_pct, "tx": res.tx_hash, "ts": time.time()})
        await self._emit(AlertType.TRADE_CLOSED, f"Posição encerrada: {pos.symbol}",
                         "", reason=reason, pnl_pct=pnl_pct,
                         entry=pos.entry_price, exit=exit_price, amount_usd=pos.amount_usd,
                         tx=res.tx_hash)
        self._save()
        await self._publish_reputation()  # SKILL: atualiza reputação on-chain (best-effort)

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
