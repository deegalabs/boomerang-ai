"""Interface Telegram do Boomerang AI (Process A).

Painel de controle do dono. Princípios de segurança:
  - NUNCA acessa chaves privadas (só conversa com o agente via métodos de controle).
  - MASTER_USER_ID pinning: ignora silenciosamente qualquer outro usuário.
  - Botões (InlineKeyboards) em vez de comandos de texto livre.
Recebe alertas do agente pelo AlertBus e renderiza mensagens ricas.
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from boomerang.agent import BoomerangAgent
from boomerang.config import Config
from boomerang.ipc import Alert, AlertBus, AlertType
from boomerang.types import AgentState

_EMOJI = {
    AlertType.STARTED: "✅", AlertType.PAUSED: "⏸️", AlertType.CIRCUIT_BREAKER: "🚨",
    AlertType.TRADE_OPENED: "📈", AlertType.TRADE_CLOSED: "🏁", AlertType.REJECTED: "🛡️",
    AlertType.HEARTBEAT: "💓", AlertType.WITHDRAWN: "🪃", AlertType.ERROR: "⚠️",
    AlertType.SCAN: "🔍", AlertType.DATA_ERROR: "⚠️",
}

# Motivo de saída → rótulo amigável.
_REASON_LABEL = {
    "SELL_STOP_LOSS": "🛑 Stop-loss disparado",
    "SELL_TRAILING": "📈 Trailing (lucro protegido)",
    "SELL_TAKE_PROFIT": "🎯 Lucro-alvo atingido",
    "SELL_BRAIN": "🧠 Saída inteligente (tese mudou)",
    "SELL_TIME_STALE": "⏳ Saída por tempo (capital parado liberado)",
    "SELL_ROTACAO": "🔁 Rotação (capital p/ oportunidade melhor)",
    "SELL_MANUAL": "🤝 Venda manual (você decidiu)",
    "liquidação": "🚨 Liquidação (circuit breaker)",
}


def _fmt_price(p) -> str:  # noqa: ANN001
    if p in (None, 0, 0.0):
        return "—"
    p = float(p)
    if p >= 1:
        return f"${p:.4f}"
    if p >= 0.0001:
        return f"${p:.6f}"
    return f"${p:.2e}"


class TelegramInterface:
    def __init__(self, config: Config, agent: BoomerangAgent, alerts: AlertBus,
                 logger: logging.Logger | None = None) -> None:
        self._cfg = config
        self._agent = agent
        self._log = logger or logging.getLogger("boomerang.interface")
        self._token = config.secrets.telegram_bot_token
        self._master = config.secrets.telegram_master_user_id
        self._app: Application | None = None
        alerts.subscribe(self._on_alert)

    # ── segurança: só o dono ─────────────────────────────────────────────────
    def _is_master(self, update: Update) -> bool:
        u = update.effective_user
        if self._master and u and u.id == self._master:
            return True
        self._log.warning("Comando ignorado de ID nao-autorizado: %s", u.id if u else "?")
        return False

    # ── alertas do agente → Telegram ─────────────────────────────────────────
    async def _on_alert(self, alert: Alert) -> None:
        if not (self._app and self._master):
            return
        emoji = _EMOJI.get(alert.type, "•")
        text = f"{emoji} *{alert.title}*"
        if alert.detail:
            text += f"\n{alert.detail}"
        d = alert.data or {}

        if alert.type == AlertType.TRADE_OPENED:
            text += f"\n💵 Comprei *${d.get('amount_usd')}* @ entrada {_fmt_price(d.get('entry'))}"
            if d.get("stop"):
                text += f"\n🛑 Stop-loss: {_fmt_price(d['stop'])} (-{d.get('stop_pct')}%)"
            if d.get("take_profit"):
                text += f"\n🎯 Lucro-alvo: {_fmt_price(d['take_profit'])} (+{d.get('take_profit_pct')}%)"
            else:
                text += "\n🎯 Lucro-alvo: deixar correr (trailing)"
            if d.get("tx"):
                text += f"\n🔗 [ver na BscScan](https://bscscan.com/tx/{d['tx']})"

        if alert.type == AlertType.TRADE_CLOSED:
            lbl = _REASON_LABEL.get(d.get("reason"), d.get("reason") or "")
            if lbl:
                text += f"\n{lbl}"
            if d.get("entry") and d.get("exit"):
                text += f"\nentrada {_fmt_price(d['entry'])} → saída {_fmt_price(d['exit'])}"
            if d.get("pnl_pct") is not None:
                emo = "🟢" if d["pnl_pct"] >= 0 else "🔴"
                text += f"\n{emo} *PnL: {d['pnl_pct']:+.2f}%*"
            if d.get("tx"):
                text += f"\n🔗 [ver na BscScan](https://bscscan.com/tx/{d['tx']})"
        try:
            await self._app.bot.send_message(self._master, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:  # noqa: BLE001
            self._log.error("Falha ao enviar alerta: %s", exc)

    # ── handlers ─────────────────────────────────────────────────────────────
    def _home_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        text = (
            "🪃 *Boomerang AI*\n\n"
            "Agente de trading na BNB Chain. Toda ação passa pelo "
            "*Protocolo dos 3 Escudos*:\n"
            "🧠 Analítico (CoinMarketCap) · 🛡️ Rede (BNB Chain) · 💼 Patrimonial (Trust Wallet)\n\n"
            "🤖 *Automático* — a IA decide sozinha quando entrar e sair.\n"
            "🎮 *Manual* — você escolhe a moeda e o tamanho; eu executo o swap real "
            "com todos os escudos de segurança (a IA não te barra).\n\n"
            "Use o menu abaixo:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 Modo Automático", callback_data="cfg"),
             InlineKeyboardButton("🎮 Modo Manual", callback_data="manual")],
            [InlineKeyboardButton("▶️ Ativar (auto)", callback_data="activate"),
             InlineKeyboardButton("📊 Status", callback_data="status")],
            [InlineKeyboardButton("⏸️ Pausar", callback_data="pause"),
             InlineKeyboardButton("🚨 Sacar Tudo e Parar", callback_data="withdraw")],
        ])
        return text, kb

    async def cmd_start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        text, kb = self._home_menu()
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def on_button(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        q = update.callback_query
        await q.answer()
        data = q.data or ""

        if data == "back_home":
            text, kb = self._home_menu()
            await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        elif data == "cfg":
            await self._step_tokens(q)
        elif data == "tok_basket":
            self._agent.configure(token_focus=list(self._agent._default_focus))
            await self._step_risk(q)
        elif data == "tok_all":
            self._agent.configure(token_focus=list(self._agent._token_addr.keys()))
            await self._step_risk(q)
        elif data.startswith("tok_"):
            self._agent.configure(token_focus=[data.split("_", 1)[1]])
            await self._step_risk(q)
        elif data.startswith("risk_"):
            pct = float(data.split("_")[1])
            mode = "conservative" if pct <= 2 else "aggressive"
            self._agent.configure(stop_loss_pct=pct, mode=mode)
            await self._step_profit(q)
        elif data.startswith("tp_"):
            self._agent.configure(take_profit_pct=float(data.split("_")[1]))
            await self._step_size(q)
        elif data.startswith("size_"):
            self._agent.configure(position_size_pct=float(data.split("_")[1]))
            await self._step_summary(q)
        elif data.startswith("sellgo_"):
            sym = data.split("_", 1)[1]
            await q.edit_message_text(f"🔴 Vendendo *{sym}* a mercado — swap real em andamento...",
                                      parse_mode=ParseMode.MARKDOWN)
            await self._agent.sell_position(sym)
        elif data == "sellallgo":
            await q.edit_message_text("🔴 Vendendo *todas* as posições a mercado...",
                                      parse_mode=ParseMode.MARKDOWN)
            n = await self._agent.sell_all_positions()
            await self._app.bot.send_message(self._master, f"✅ {n} posição(ões) vendida(s).")
        elif data.startswith("sell_"):
            sym = data.split("_", 1)[1]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Confirmar venda de {sym}", callback_data=f"sellgo_{sym}")],
                [InlineKeyboardButton("↩️ Voltar", callback_data="status")],
            ])
            await q.edit_message_text(
                f"🔴 *Vender {sym}?*\n\nVai vender a posição inteira *a mercado* (preço atual).\n"
                "O agente continua operando depois (não trava). Confirma?",
                reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        elif data == "sellall":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirmar venda de TUDO", callback_data="sellallgo")],
                [InlineKeyboardButton("↩️ Voltar", callback_data="status")],
            ])
            await q.edit_message_text("🔴 *Vender TODAS as posições?* A mercado, agora. Confirma?",
                                      reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        elif data == "manual":
            await self._manual_pick_coin(q)
        elif data.startswith("mbuy_"):
            await self._manual_pick_size(q, data.split("_", 1)[1])
        elif data.startswith("msz_"):
            _, sym, pct = data.split("_")
            await self._manual_confirm(q, sym, float(pct))
        elif data.startswith("mgo_"):
            _, sym, pct = data.split("_")
            await self._manual_execute(q, sym, float(pct))
        elif data == "activate":
            await self._agent.start()
            await q.edit_message_text("▶️ Agente ativado. Você receberá alertas a cada ciclo.")
        elif data == "pause":
            await self._agent.pause()
            await q.edit_message_text("⏸️ Agente pausado. Use /start para retomar.")
        elif data == "status":
            await self._send_status(q)
        elif data == "withdraw":
            await q.edit_message_text("🪃 Sacando tudo para sua carteira e parando...")
            await self._agent.withdraw_all()

    # ── passos do assistente de configuração ─────────────────────────────────
    async def _step_tokens(self, q) -> None:  # noqa: ANN001
        n = len(self._agent._default_focus)
        rows = [
            [InlineKeyboardButton(f"🧺 Cesta recomendada ({n} líquidas)", callback_data="tok_basket")],
            [InlineKeyboardButton("🌐 Todas as elegíveis (mais cara)", callback_data="tok_all")],
        ]
        line = []
        for sym in self._agent._token_addr.keys():  # todas as moedas-foco elegíveis
            line.append(InlineKeyboardButton(sym, callback_data=f"tok_{sym}"))
            if len(line) == 4:
                rows.append(line); line = []
        if line:
            rows.append(line)
        await q.edit_message_text(
            "⚙️ *Configuração — Passo 1 de 4*\nEm que o agente deve focar a análise?\n\n"
            "🧺 *Cesta recomendada* — várias moedas líquidas (melhor p/ o modo autônomo "
            "ter oportunidades). \n"
            "🪙 *Uma moeda* — restringe o autônomo a ela só (use se quiser vigiar 1 ativo).",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

    async def _step_risk(self, q) -> None:  # noqa: ANN001
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 Conservador (stop 2%)", callback_data="risk_2"),
             InlineKeyboardButton("🟡 Moderado (stop 4%)", callback_data="risk_4")],
            [InlineKeyboardButton("🔵 Padrão (stop 5%)", callback_data="risk_5")],
            [InlineKeyboardButton("↩️ Voltar", callback_data="cfg")],
        ])
        foco = ", ".join(self._agent.token_focus)
        await q.edit_message_text(
            f"⚙️ *Configuração — Passo 2 de 4*\nFoco: *{foco}*\n\n"
            "🛑 Qual o *Stop-Loss* (quanto aceita perder por operação antes de vender)?",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _step_profit(self, q) -> None:  # noqa: ANN001
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 +5%", callback_data="tp_5"),
             InlineKeyboardButton("🎯 +10%", callback_data="tp_10"),
             InlineKeyboardButton("🎯 +15%", callback_data="tp_15")],
            [InlineKeyboardButton("📈 Deixar correr (trailing)", callback_data="tp_0")],
            [InlineKeyboardButton("↩️ Voltar", callback_data="cfg")],
        ])
        await q.edit_message_text(
            f"⚙️ *Configuração — Passo 3 de 4*\nStop-Loss: *{self._agent.stop_loss_pct:.0f}%*\n\n"
            "🎯 Qual o *lucro-alvo* (quando o ganho chegar nesse nível, eu vendo e realizo)?\n\n"
            "_\"Deixar correr\" = sem teto fixo; eu seguro com o trailing pra capturar "
            "altas maiores, protegendo o lucro já feito._",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _step_size(self, q) -> None:  # noqa: ANN001
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 10%", callback_data="size_10"),
             InlineKeyboardButton("💵 25%", callback_data="size_25"),
             InlineKeyboardButton("💪 50%", callback_data="size_50")],
            [InlineKeyboardButton("↩️ Voltar", callback_data="cfg")],
        ])
        await q.edit_message_text(
            f"⚙️ *Configuração — Passo 4 de 4*\nLucro-alvo definido.\n\n"
            "📊 Qual o *tamanho de cada trade* (quanto da sua banca eu aposto por operação)?\n\n"
            "_Banca pequena pede tamanho maior: trades de 5% somem no gás. "
            "Mesmo apostando mais, o disjuntor de drawdown continua te protegendo._",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _step_summary(self, q) -> None:  # noqa: ANN001
        a = self._agent
        corte = self._cfg.min_confidence_score
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Ativar Agora", callback_data="activate")],
            [InlineKeyboardButton("⚙️ Reconfigurar", callback_data="cfg")],
        ])
        alvo = f"+{a.take_profit_pct:.0f}%" if a.take_profit_pct else "deixar correr (trailing)"
        await q.edit_message_text(
            "✅ *Pronto para ativar*\n\n"
            f"🎯 Foco: *{', '.join(a.token_focus)}*\n"
            f"🛑 Stop-Loss: *{a.stop_loss_pct:.0f}%*\n"
            f"🎯 Lucro-alvo: *{alvo}*\n"
            f"🧠 Modo: *{a.mode}* (compra se score ≥ {corte}, *adaptativo* ao mercado)\n"
            f"📊 Tamanho/trade: *{a.position_size_pct:.0f}%* da banca\n\n"
            "Confirme para eu começar a operar:",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    # ── modo manual: você escolhe a moeda e o tamanho ────────────────────────
    async def _manual_pick_coin(self, q) -> None:  # noqa: ANN001
        rows, line = [], []
        for sym in self._agent.token_focus:  # set curado e líquido que você configurou
            line.append(InlineKeyboardButton(sym, callback_data=f"mbuy_{sym}"))
            if len(line) == 4:
                rows.append(line); line = []
        if line:
            rows.append(line)
        rows.append([InlineKeyboardButton("↩️ Voltar", callback_data="back_home")])
        await q.edit_message_text(
            "🎮 *Modo Manual — Passo 1 de 2*\n\n"
            "Qual moeda você quer *comprar agora*?\n\n"
            "_Aqui é você quem decide — a IA não precisa aprovar. Os escudos de "
            "segurança (whitelist, anti-honeypot/taxa, slippage, anti-drain e o "
            "disjuntor de drawdown) continuam ativos._",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

    async def _manual_pick_size(self, q, sym: str) -> None:  # noqa: ANN001
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 10%", callback_data=f"msz_{sym}_10"),
             InlineKeyboardButton("💵 25%", callback_data=f"msz_{sym}_25"),
             InlineKeyboardButton("💪 50%", callback_data=f"msz_{sym}_50")],
            [InlineKeyboardButton(f"🔥 Tudo em {sym} (100%)", callback_data=f"msz_{sym}_100")],
            [InlineKeyboardButton("↩️ Voltar", callback_data="manual")],
        ])
        await q.edit_message_text(
            f"🎮 *Modo Manual — Passo 2 de 2*\nMoeda: *{sym}*\n\n"
            "💰 *Quanto da banca* você quer apostar nesta operação?\n\n"
            "_No manual não há teto automático: se quiser, vai *all-in*. "
            "O disjuntor de drawdown ainda te protege de perda catastrófica._",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _manual_confirm(self, q, sym: str, pct: float) -> None:  # noqa: ANN001
        eq = self._agent._last_equity or 0.0
        approx = f"≈ *${eq * pct / 100.0:.2f}*" if eq > 0 else "_(valor exato calculado na hora pelo seu saldo)_"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Confirmar e comprar {sym}", callback_data=f"mgo_{sym}_{pct:.0f}")],
            [InlineKeyboardButton("↩️ Trocar tamanho", callback_data=f"mbuy_{sym}"),
             InlineKeyboardButton("❌ Cancelar", callback_data="back_home")],
        ])
        await q.edit_message_text(
            f"⚠️ *Confirmação — você está no comando*\n\n"
            f"Vou comprar *{sym}* com *{pct:.0f}%* da banca ({approx}).\n\n"
            "Este é um trade *manual*: ele *ignora a nota da IA* — você assume o risco "
            "desta decisão. O que *continua valendo*:\n"
            "🛡️ whitelist · anti-honeypot/taxa · slippage máx · anti-drain\n"
            "🚨 disjuntor de drawdown (proteção contra perda catastrófica)\n"
            f"🛑 stop-loss de -{self._agent.stop_loss_pct:.0f}% será monitorado normalmente\n\n"
            "Confirma?",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _manual_execute(self, q, sym: str, pct: float) -> None:  # noqa: ANN001
        await q.edit_message_text(
            f"🟡 Compra manual de *{sym}* ({pct:.0f}% da banca) — swap real em andamento...\n"
            "_Você receberá a confirmação com o link da BscScan._",
            parse_mode=ParseMode.MARKDOWN)
        if self._agent.state not in (AgentState.SCANNING, AgentState.IN_POSITION):
            await self._agent.start()
        await self._agent.force_buy(sym, size_pct=pct)

    async def cmd_status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await self._send_status(update)

    async def _send_status(self, target) -> None:  # noqa: ANN001
        s = await self._agent.status()
        eq = f"${s['equity_usd']:.2f}" if s["equity_usd"] is not None else "n/d"
        dd = f"{s['drawdown_pct']:.1f}%" if s["drawdown_pct"] is not None else "n/d"
        tp = s.get("take_profit_pct") or 0
        tp_txt = f"alvo +{tp:.0f}%" if tp else "alvo: deixa correr"
        lines = [
            "📊 *Status do Boomerang AI*",
            f"Estado: *{s['state']}*",
            f"💰 Patrimônio: *{eq}*  ·  Drawdown: {dd}",
            f"⚙️ Stop -{s['stop_loss_pct']:.0f}% · 🎯 {tp_txt} · modo {self._agent.mode}",
        ]
        det = s.get("positions_detail") or []
        if det:
            lines.append("\n*📌 Posições abertas:*")
            for p in det:
                pnl = f"{p['pnl_pct']:+.2f}%" if p["pnl_pct"] is not None else "—"
                emo = "🟢" if (p["pnl_pct"] or 0) >= 0 else "🔴"
                tpp = _fmt_price(p["take_profit"]) if p["take_profit"] else "trailing"
                trail = " · 📈trailing ON" if p["trailing_active"] else ""
                lines.append(
                    f"{emo} *{p['symbol']}*  ${p['amount_usd']:.2f}  ·  PnL *{pnl}*{trail}\n"
                    f"   entrada {_fmt_price(p['entry'])} → agora {_fmt_price(p['current'])}\n"
                    f"   🛑 stop {_fmt_price(p['stop'])}  ·  🎯 alvo {tpp}"
                )
        else:
            lines.append("\n📌 Nenhuma posição aberta no momento.")
        lines.append(f"\n🎯 Foco ({len(s['token_focus'])} moedas): {', '.join(s['token_focus'])}")
        text = "\n".join(lines)
        # Botões de VENDA por posição (dinheiro real → exige confirmação no clique seguinte).
        rows = []
        for p in det:
            pnl = f"{p['pnl_pct']:+.1f}%" if p["pnl_pct"] is not None else "—"
            rows.append([InlineKeyboardButton(f"🔴 Vender {p['symbol']} (PnL {pnl})",
                                              callback_data=f"sell_{p['symbol']}")])
        if len(det) > 1:
            rows.append([InlineKeyboardButton("🔴 Vender TUDO", callback_data="sellall")])
        rows.append([InlineKeyboardButton("🔄 Atualizar", callback_data="status")])
        kb = InlineKeyboardMarkup(rows)
        send = target.edit_message_text if hasattr(target, "edit_message_text") else target.message.reply_text
        await send(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    async def cmd_panic(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text("🚨 Pânico: liquidando tudo e travando o agente...")
        await self._agent.panic("Comando manual /panic do dono.")

    async def cmd_pause(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await self._agent.pause()
        await update.message.reply_text("⏸️ Agente pausado. Use /start para retomar.")

    async def cmd_buy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        if not ctx.args:
            await update.message.reply_text(
                "Uso: /buy SIMBOLO [%]  (ex.: /buy ETH  ou  /buy FLOKI 100 para all-in)")
            return
        symbol = ctx.args[0].upper()
        size_pct: float | None = None
        if len(ctx.args) > 1:
            try:
                size_pct = max(0.0, min(float(ctx.args[1].rstrip("%")), 100.0))
            except ValueError:
                await update.message.reply_text("Tamanho inválido. Ex.: /buy FLOKI 50")
                return
        tam = f" ({size_pct:.0f}% da banca)" if size_pct is not None else ""
        await update.message.reply_text(
            f"🟡 Compra manual de {symbol}{tam} (validação) — swap real em andamento...")
        if self._agent.state not in (AgentState.SCANNING, AgentState.IN_POSITION):
            await self._agent.start()
        await self._agent.force_buy(symbol, size_pct=size_pct)

    async def cmd_reiniciar(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text("🔄 Destravando e reiniciando a sessão...")
        await self._agent.restart_session()

    async def cmd_registrar(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text(
            "🏁 Registrando a carteira do agente na competição (on-chain)...")
        try:
            res = await self._agent.register_competition()
            tx = res.get("transactionHash") or res.get("tx") if isinstance(res, dict) else None
            msg = "✅ *Registro enviado!*" + (f"\n🔗 [BscScan](https://bscscan.com/tx/{tx})" if tx else "")
            if isinstance(res, dict) and res.get("error"):
                msg = f"⚠️ Resposta: {res['error']}"
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN,
                                            disable_web_page_preview=True)
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(
                f"❌ Falha no registro: {exc}\n\nVerifique se já está registrado com /competicao.")

    async def cmd_competicao(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text("🔎 Consultando status da competição...")
        try:
            res = await self._agent.competition_status()
            import json as _json
            await update.message.reply_text(
                f"🏁 *Status da competição:*\n```\n{_json.dumps(res, ensure_ascii=False, indent=2)[:1500]}\n```",
                parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"❌ Não consegui consultar: {exc}")

    async def cmd_dashboard(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        import os
        if not self._is_master(update):
            return
        token = os.getenv("DASHBOARD_TOKEN")
        base = os.getenv("DASHBOARD_BASE_URL", "http://localhost:8080")
        if not token:
            await update.message.reply_text("Dashboard não configurado (DASHBOARD_TOKEN ausente no .env).")
            return
        url = f"{base}/dash?key={token}"
        await update.message.reply_text(
            f"📊 *Seu painel (só-leitura):*\n{url}\n\n⚠️ Link privado — não compartilhe.",
            parse_mode=ParseMode.MARKDOWN)

    _HELP = (
        "🪃 *Comandos do Boomerang AI*\n\n"
        "• /start — menu principal (🤖 Automático · 🎮 Manual · Status · Pausar · Sacar)\n"
        "• /status — situação + *botões de VENDER* cada posição\n"
        "• /buy SÍMBOLO [%] — compra manual (ex.: `/buy CAKE` ou `/buy FLOKI 100` p/ all-in)\n"
        "• /pausar (ou /parar, /stop) — pausa o agente (retoma com /start)\n"
        "• /panic — vende tudo *e trava* o agente (emergência)\n"
        "• /reiniciar — *destrava* após /panic ou saque e volta a operar\n"
        "• /dashboard — link do painel só-leitura\n"
        "• /registrar — registra a carteira na competição (rodar 1x antes de 22/jun)\n"
        "• /competicao — status do registro na competição\n"
        "• /ajuda — esta lista\n\n"
        "_Para vender sem travar, use os botões 🔴 dentro do /status._"
    )

    async def cmd_help(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text(self._HELP, parse_mode=ParseMode.MARKDOWN)

    async def cmd_unknown(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text(
            "❓ Não entendi esse comando. Veja /ajuda para a lista completa.\n\n" + self._HELP,
            parse_mode=ParseMode.MARKDOWN)

    # ── ciclo de vida ────────────────────────────────────────────────────────
    def build(self) -> Application:
        if not self._token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN ausente no .env")
        app = Application.builder().token(self._token).build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("panic", self.cmd_panic))
        app.add_handler(CommandHandler(["pausar", "parar", "stop"], self.cmd_pause))
        app.add_handler(CommandHandler(["reiniciar", "restart", "destravar"], self.cmd_reiniciar))
        app.add_handler(CommandHandler("buy", self.cmd_buy))
        app.add_handler(CommandHandler("dashboard", self.cmd_dashboard))
        app.add_handler(CommandHandler(["registrar", "register"], self.cmd_registrar))
        app.add_handler(CommandHandler(["competicao", "compete"], self.cmd_competicao))
        app.add_handler(CommandHandler(["ajuda", "help", "comandos"], self.cmd_help))
        app.add_handler(CallbackQueryHandler(self.on_button))
        # fallback: qualquer comando/texto nao reconhecido -> orienta o usuario
        app.add_handler(MessageHandler(filters.COMMAND | (filters.TEXT & ~filters.COMMAND),
                                       self.cmd_unknown))
        self._app = app
        return app

    async def start_polling(self) -> None:
        app = self._app or self.build()
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        self._log.info("Bot do Telegram em polling.")
