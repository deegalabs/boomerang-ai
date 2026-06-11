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
    async def cmd_start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        text = (
            "🪃 *Boomerang AI*\n\n"
            "Agente de trading autônomo na BNB Chain. Toda ação passa pelo "
            "*Protocolo dos 3 Escudos*:\n"
            "🧠 Analítico (CoinMarketCap) · 🛡️ Rede (BNB Chain) · 💼 Patrimonial (Trust Wallet)\n\n"
            "Use o menu abaixo:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Configurar", callback_data="cfg"),
             InlineKeyboardButton("▶️ Ativar", callback_data="activate")],
            [InlineKeyboardButton("📊 Status", callback_data="status"),
             InlineKeyboardButton("⏸️ Pausar", callback_data="pause")],
            [InlineKeyboardButton("🚨 Sacar Tudo e Parar", callback_data="withdraw")],
        ])
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def on_button(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        q = update.callback_query
        await q.answer()
        data = q.data or ""

        if data == "cfg":
            await self._step_tokens(q)
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
        rows = [[InlineKeyboardButton("🌐 Todos os líquidos", callback_data="tok_all")]]
        line = []
        for sym in self._agent._token_addr.keys():  # todas as moedas-foco elegíveis
            line.append(InlineKeyboardButton(sym, callback_data=f"tok_{sym}"))
            if len(line) == 4:
                rows.append(line); line = []
        if line:
            rows.append(line)
        await q.edit_message_text(
            "⚙️ *Configuração — Passo 1 de 4*\nEm qual moeda devo focar a análise?",
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
        send = target.edit_message_text if hasattr(target, "edit_message_text") else target.message.reply_text
        await send(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

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
            await update.message.reply_text("Uso: /buy SIMBOLO  (ex.: /buy ETH)")
            return
        symbol = ctx.args[0].upper()
        await update.message.reply_text(f"🟡 Compra manual de {symbol} (validação) — swap real em andamento...")
        if self._agent.state not in (AgentState.SCANNING, AgentState.IN_POSITION):
            await self._agent.start()
        await self._agent.force_buy(symbol)

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

    async def cmd_unknown(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text(
            "❓ Não entendi esse comando.\n\n"
            "Use:\n• /start — menu\n• /status — situação\n"
            "• /pausar (ou /parar, /stop) — parar a operação\n• /panic — liquidar tudo e travar")

    # ── ciclo de vida ────────────────────────────────────────────────────────
    def build(self) -> Application:
        if not self._token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN ausente no .env")
        app = Application.builder().token(self._token).build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("panic", self.cmd_panic))
        app.add_handler(CommandHandler(["pausar", "parar", "stop"], self.cmd_pause))
        app.add_handler(CommandHandler("buy", self.cmd_buy))
        app.add_handler(CommandHandler("dashboard", self.cmd_dashboard))
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
