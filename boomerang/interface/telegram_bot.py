"""Boomerang AI Telegram interface (Process A).

The owner's control panel. Security principles:
  - NEVER accesses private keys (only talks to the agent via control methods).
  - MASTER_USER_ID pinning: silently ignores any other user.
  - Buttons (InlineKeyboards) instead of free-text commands.
Receives agent alerts via the AlertBus and renders rich messages.
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

# Exit reason → friendly label.
_REASON_LABEL = {
    "SELL_STOP_LOSS": "🛑 Stop-loss triggered",
    "SELL_TRAILING": "📈 Trailing (profit protected)",
    "SELL_TAKE_PROFIT": "🎯 Take-profit reached",
    "SELL_BRAIN": "🧠 Smart exit (thesis changed)",
    "SELL_TIME_STALE": "⏳ Time-based exit (idle capital freed)",
    "SELL_ROTACAO": "🔁 Rotation (capital to a better opportunity)",
    "SELL_MANUAL": "🤝 Manual sell (you decided)",
    "liquidação": "🚨 Liquidation (circuit breaker)",
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

    # ── security: owner only ─────────────────────────────────────────────────
    def _is_master(self, update: Update) -> bool:
        u = update.effective_user
        if self._master and u and u.id == self._master:
            return True
        self._log.warning("Command ignored from unauthorized ID: %s", u.id if u else "?")
        return False

    # ── agent alerts → Telegram ─────────────────────────────────────────
    async def _on_alert(self, alert: Alert) -> None:
        if not (self._app and self._master):
            return
        emoji = _EMOJI.get(alert.type, "•")
        text = f"{emoji} *{alert.title}*"
        if alert.detail:
            text += f"\n{alert.detail}"
        d = alert.data or {}

        if alert.type == AlertType.TRADE_OPENED:
            text += f"\n💵 Bought *${d.get('amount_usd')}* @ entry {_fmt_price(d.get('entry'))}"
            if d.get("stop"):
                text += f"\n🛑 Stop-loss: {_fmt_price(d['stop'])} (-{d.get('stop_pct')}%)"
            if d.get("take_profit"):
                text += f"\n🎯 Take-profit: {_fmt_price(d['take_profit'])} (+{d.get('take_profit_pct')}%)"
            else:
                text += "\n🎯 Take-profit: let it run (trailing)"
            if d.get("tx"):
                text += f"\n🔗 [view on BscScan](https://bscscan.com/tx/{d['tx']})"

        if alert.type == AlertType.TRADE_CLOSED:
            lbl = _REASON_LABEL.get(d.get("reason"), d.get("reason") or "")
            if lbl:
                text += f"\n{lbl}"
            if d.get("entry") and d.get("exit"):
                text += f"\nentry {_fmt_price(d['entry'])} → exit {_fmt_price(d['exit'])}"
            if d.get("pnl_pct") is not None:
                emo = "🟢" if d["pnl_pct"] >= 0 else "🔴"
                text += f"\n{emo} *PnL: {d['pnl_pct']:+.2f}%*"
            if d.get("tx"):
                text += f"\n🔗 [view on BscScan](https://bscscan.com/tx/{d['tx']})"
        try:
            await self._app.bot.send_message(self._master, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:  # noqa: BLE001
            self._log.error("Failed to send alert: %s", exc)

    # ── handlers ─────────────────────────────────────────────────────────────
    def _home_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        text = (
            "🪃 *Boomerang AI*\n\n"
            "Trading agent on BNB Chain. Every action passes through the "
            "*3-Shield Protocol*:\n"
            "🧠 Analytical (CoinMarketCap) · 🛡️ Network (BNB Chain) · 💼 Wallet (Trust Wallet)\n\n"
            "🤖 *Automatic* — the AI decides on its own when to enter and exit.\n"
            "🎮 *Manual* — you pick the coin and the size; I execute the real swap "
            "with all the security shields (the AI doesn't block you).\n\n"
            "Use the menu below:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 Automatic Mode", callback_data="cfg"),
             InlineKeyboardButton("🎮 Manual Mode", callback_data="manual")],
            [InlineKeyboardButton("▶️ Activate (auto)", callback_data="activate"),
             InlineKeyboardButton("📊 Status", callback_data="status")],
            [InlineKeyboardButton("⏸️ Pause", callback_data="pause"),
             InlineKeyboardButton("🚨 Withdraw All and Stop", callback_data="withdraw")],
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
            await q.edit_message_text(f"🔴 Selling *{sym}* at market — real swap in progress...",
                                      parse_mode=ParseMode.MARKDOWN)
            await self._agent.sell_position(sym)
        elif data == "sellallgo":
            await q.edit_message_text("🔴 Selling *all* positions at market...",
                                      parse_mode=ParseMode.MARKDOWN)
            n = await self._agent.sell_all_positions()
            await self._app.bot.send_message(self._master, f"✅ {n} position(s) sold.")
        elif data.startswith("sell_"):
            sym = data.split("_", 1)[1]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Confirm selling {sym}", callback_data=f"sellgo_{sym}")],
                [InlineKeyboardButton("↩️ Back", callback_data="status")],
            ])
            await q.edit_message_text(
                f"🔴 *Sell {sym}?*\n\nThis will sell the entire position *at market* (current price).\n"
                "The agent keeps trading afterwards (no halt). Confirm?",
                reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        elif data == "sellall":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm selling EVERYTHING", callback_data="sellallgo")],
                [InlineKeyboardButton("↩️ Back", callback_data="status")],
            ])
            await q.edit_message_text("🔴 *Sell ALL positions?* At market, now. Confirm?",
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
            await q.edit_message_text("▶️ Agent activated. You'll receive alerts on every cycle.")
        elif data == "pause":
            await self._agent.pause()
            await q.edit_message_text("⏸️ Agent paused. Use /start to resume.")
        elif data == "status":
            await self._send_status(q)
        elif data == "withdraw":
            await q.edit_message_text("🪃 Withdrawing everything to your wallet and stopping...")
            await self._agent.withdraw_all()

    # ── configuration wizard steps ─────────────────────────────────
    async def _step_tokens(self, q) -> None:  # noqa: ANN001
        n = len(self._agent._default_focus)
        rows = [
            [InlineKeyboardButton(f"🧺 Recommended basket ({n} liquid)", callback_data="tok_basket")],
            [InlineKeyboardButton("🌐 All eligible (more expensive)", callback_data="tok_all")],
        ]
        line = []
        for sym in self._agent._token_addr.keys():  # all eligible focus coins
            line.append(InlineKeyboardButton(sym, callback_data=f"tok_{sym}"))
            if len(line) == 4:
                rows.append(line)
                line = []
        if line:
            rows.append(line)
        await q.edit_message_text(
            "⚙️ *Configuration — Step 1 of 4*\nWhat should the agent focus its analysis on?\n\n"
            "🧺 *Recommended basket* — several liquid coins (better for the autonomous mode "
            "to have opportunities). \n"
            "🪙 *A single coin* — restricts the autonomous mode to it alone (use if you want to watch 1 asset).",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

    async def _step_risk(self, q) -> None:  # noqa: ANN001
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 Conservative (stop 2%)", callback_data="risk_2"),
             InlineKeyboardButton("🟡 Moderate (stop 4%)", callback_data="risk_4")],
            [InlineKeyboardButton("🔵 Default (stop 5%)", callback_data="risk_5")],
            [InlineKeyboardButton("↩️ Back", callback_data="cfg")],
        ])
        foco = ", ".join(self._agent.token_focus)
        await q.edit_message_text(
            f"⚙️ *Configuration — Step 2 of 4*\nFocus: *{foco}*\n\n"
            "🛑 What *Stop-Loss* (how much you accept losing per trade before selling)?",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _step_profit(self, q) -> None:  # noqa: ANN001
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 +5%", callback_data="tp_5"),
             InlineKeyboardButton("🎯 +10%", callback_data="tp_10"),
             InlineKeyboardButton("🎯 +15%", callback_data="tp_15")],
            [InlineKeyboardButton("📈 Let it run (trailing)", callback_data="tp_0")],
            [InlineKeyboardButton("↩️ Back", callback_data="cfg")],
        ])
        await q.edit_message_text(
            f"⚙️ *Configuration — Step 3 of 4*\nStop-Loss: *{self._agent.stop_loss_pct:.0f}%*\n\n"
            "🎯 What *take-profit* (when the gain reaches this level, I sell and lock it in)?\n\n"
            "_\"Let it run\" = no fixed ceiling; I hold with the trailing to capture "
            "bigger rallies, protecting the profit already made._",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _step_size(self, q) -> None:  # noqa: ANN001
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 10%", callback_data="size_10"),
             InlineKeyboardButton("💵 25%", callback_data="size_25"),
             InlineKeyboardButton("💪 50%", callback_data="size_50")],
            [InlineKeyboardButton("↩️ Back", callback_data="cfg")],
        ])
        await q.edit_message_text(
            "⚙️ *Configuration — Step 4 of 4*\nTake-profit set.\n\n"
            "📊 What *size for each trade* (how much of your bankroll I bet per trade)?\n\n"
            "_A small bankroll calls for a bigger size: 5% trades vanish in gas. "
            "Even betting more, the drawdown circuit breaker keeps protecting you._",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _step_summary(self, q) -> None:  # noqa: ANN001
        a = self._agent
        corte = self._cfg.min_confidence_score
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Activate Now", callback_data="activate")],
            [InlineKeyboardButton("⚙️ Reconfigure", callback_data="cfg")],
        ])
        alvo = f"+{a.take_profit_pct:.0f}%" if a.take_profit_pct else "let it run (trailing)"
        await q.edit_message_text(
            "✅ *Ready to activate*\n\n"
            f"🎯 Focus: *{', '.join(a.token_focus)}*\n"
            f"🛑 Stop-Loss: *{a.stop_loss_pct:.0f}%*\n"
            f"🎯 Take-profit: *{alvo}*\n"
            f"🧠 Mode: *{a.mode}* (buys if score ≥ {corte}, *adaptive* to the market)\n"
            f"📊 Size/trade: *{a.position_size_pct:.0f}%* of the bankroll\n\n"
            "Confirm for me to start trading:",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    # ── manual mode: you pick the coin and the size ────────────────────────
    async def _manual_pick_coin(self, q) -> None:  # noqa: ANN001
        rows, line = [], []
        for sym in self._agent.token_focus:  # the curated, liquid set you configured
            line.append(InlineKeyboardButton(sym, callback_data=f"mbuy_{sym}"))
            if len(line) == 4:
                rows.append(line)
                line = []
        if line:
            rows.append(line)
        rows.append([InlineKeyboardButton("↩️ Back", callback_data="back_home")])
        await q.edit_message_text(
            "🎮 *Manual Mode — Step 1 of 2*\n\n"
            "Which coin do you want to *buy now*?\n\n"
            "_Here you're the one deciding — the AI doesn't need to approve. The security "
            "shields (whitelist, anti-honeypot/tax, slippage, anti-drain and the "
            "drawdown circuit breaker) stay active._",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

    async def _manual_pick_size(self, q, sym: str) -> None:  # noqa: ANN001
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 10%", callback_data=f"msz_{sym}_10"),
             InlineKeyboardButton("💵 25%", callback_data=f"msz_{sym}_25"),
             InlineKeyboardButton("💪 50%", callback_data=f"msz_{sym}_50")],
            [InlineKeyboardButton(f"🔥 All in {sym} (100%)", callback_data=f"msz_{sym}_100")],
            [InlineKeyboardButton("↩️ Back", callback_data="manual")],
        ])
        await q.edit_message_text(
            f"🎮 *Manual Mode — Step 2 of 2*\nCoin: *{sym}*\n\n"
            "💰 *How much of the bankroll* do you want to bet on this trade?\n\n"
            "_In manual there's no automatic ceiling: if you want, go *all-in*. "
            "The drawdown circuit breaker still protects you from catastrophic loss._",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _manual_confirm(self, q, sym: str, pct: float) -> None:  # noqa: ANN001
        eq = self._agent._last_equity or 0.0
        approx = f"≈ *${eq * pct / 100.0:.2f}*" if eq > 0 else "_(exact value computed at execution from your balance)_"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Confirm and buy {sym}", callback_data=f"mgo_{sym}_{pct:.0f}")],
            [InlineKeyboardButton("↩️ Change size", callback_data=f"mbuy_{sym}"),
             InlineKeyboardButton("❌ Cancel", callback_data="back_home")],
        ])
        await q.edit_message_text(
            f"⚠️ *Confirmation — you're in command*\n\n"
            f"I'll buy *{sym}* with *{pct:.0f}%* of the bankroll ({approx}).\n\n"
            "This is a *manual* trade: it *ignores the AI score* — you take the risk "
            "of this decision. What *still applies*:\n"
            "🛡️ whitelist · anti-honeypot/tax · max slippage · anti-drain\n"
            "🚨 drawdown circuit breaker (protection against catastrophic loss)\n"
            f"🛑 stop-loss of -{self._agent.stop_loss_pct:.0f}% will be monitored normally\n\n"
            "Confirm?",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def _manual_execute(self, q, sym: str, pct: float) -> None:  # noqa: ANN001
        await q.edit_message_text(
            f"🟡 Manual buy of *{sym}* ({pct:.0f}% of the bankroll) — real swap in progress...\n"
            "_You'll receive the confirmation with the BscScan link._",
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
        eq = f"${s['equity_usd']:.2f}" if s["equity_usd"] is not None else "n/a"
        dd = f"{s['drawdown_pct']:.1f}%" if s["drawdown_pct"] is not None else "n/a"
        tp = s.get("take_profit_pct") or 0
        tp_txt = f"target +{tp:.0f}%" if tp else "target: let it run"
        lines = [
            "📊 *Boomerang AI Status*",
            f"State: *{s['state']}*",
            f"💰 Equity: *{eq}*  ·  Drawdown: {dd}",
            f"⚙️ Stop -{s['stop_loss_pct']:.0f}% · 🎯 {tp_txt} · mode {self._agent.mode}",
        ]
        det = s.get("positions_detail") or []
        if det:
            lines.append("\n*📌 Open positions:*")
            for p in det:
                pnl = f"{p['pnl_pct']:+.2f}%" if p["pnl_pct"] is not None else "—"
                emo = "🟢" if (p["pnl_pct"] or 0) >= 0 else "🔴"
                tpp = _fmt_price(p["take_profit"]) if p["take_profit"] else "trailing"
                trail = " · 📈trailing ON" if p["trailing_active"] else ""
                lines.append(
                    f"{emo} *{p['symbol']}*  ${p['amount_usd']:.2f}  ·  PnL *{pnl}*{trail}\n"
                    f"   entry {_fmt_price(p['entry'])} → now {_fmt_price(p['current'])}\n"
                    f"   🛑 stop {_fmt_price(p['stop'])}  ·  🎯 target {tpp}"
                )
        else:
            lines.append("\n📌 No open positions at the moment.")
        lines.append(f"\n🎯 Focus ({len(s['token_focus'])} coins): {', '.join(s['token_focus'])}")
        text = "\n".join(lines)
        # Per-position SELL buttons (real money → requires confirmation on the next click).
        rows = []
        for p in det:
            pnl = f"{p['pnl_pct']:+.1f}%" if p["pnl_pct"] is not None else "—"
            rows.append([InlineKeyboardButton(f"🔴 Sell {p['symbol']} (PnL {pnl})",
                                              callback_data=f"sell_{p['symbol']}")])
        if len(det) > 1:
            rows.append([InlineKeyboardButton("🔴 Sell EVERYTHING", callback_data="sellall")])
        rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="status")])
        kb = InlineKeyboardMarkup(rows)
        send = target.edit_message_text if hasattr(target, "edit_message_text") else target.message.reply_text
        await send(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    async def cmd_panic(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text("🚨 Panic: liquidating everything and halting the agent...")
        await self._agent.panic("Owner's manual /panic command.")

    async def cmd_pause(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await self._agent.pause()
        await update.message.reply_text("⏸️ Agent paused. Use /start to resume.")

    async def cmd_buy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        if not ctx.args:
            await update.message.reply_text(
                "Usage: /buy SYMBOL [%]  (e.g. /buy ETH  or  /buy FLOKI 100 for all-in)")
            return
        symbol = ctx.args[0].upper()
        size_pct: float | None = None
        if len(ctx.args) > 1:
            try:
                size_pct = max(0.0, min(float(ctx.args[1].rstrip("%")), 100.0))
            except ValueError:
                await update.message.reply_text("Invalid size. E.g. /buy FLOKI 50")
                return
        tam = f" ({size_pct:.0f}% of the bankroll)" if size_pct is not None else ""
        await update.message.reply_text(
            f"🟡 Manual buy of {symbol}{tam} (validation) — real swap in progress...")
        if self._agent.state not in (AgentState.SCANNING, AgentState.IN_POSITION):
            await self._agent.start()
        await self._agent.force_buy(symbol, size_pct=size_pct)

    async def cmd_reiniciar(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text("🔄 Unlocking and restarting the session...")
        await self._agent.restart_session()

    async def cmd_registrar(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text(
            "🏁 Registering the agent's wallet in the competition (on-chain)...")
        try:
            res = await self._agent.register_competition()
            tx = res.get("transactionHash") or res.get("tx") if isinstance(res, dict) else None
            msg = "✅ *Registration submitted!*" + (f"\n🔗 [BscScan](https://bscscan.com/tx/{tx})" if tx else "")
            if isinstance(res, dict) and res.get("error"):
                msg = f"⚠️ Response: {res['error']}"
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN,
                                            disable_web_page_preview=True)
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(
                f"❌ Registration failed: {exc}\n\nCheck whether it's already registered with /competicao.")

    async def cmd_competicao(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text("🔎 Querying competition status...")
        try:
            res = await self._agent.competition_status()
            import json as _json
            await update.message.reply_text(
                f"🏁 *Competition status:*\n```\n{_json.dumps(res, ensure_ascii=False, indent=2)[:1500]}\n```",
                parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"❌ Couldn't query: {exc}")

    async def cmd_dashboard(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        import os
        if not self._is_master(update):
            return
        base = os.getenv("PUBLIC_SITE_URL", "https://boomerang-ai-production.up.railway.app").rstrip("/")
        url = f"{base}/live"
        await update.message.reply_text(
            f"📊 *Live panel — read-only, on-chain proof:*\n{url}",
            parse_mode=ParseMode.MARKDOWN)

    _HELP = (
        "🪃 *Boomerang AI commands*\n\n"
        "• /start — main menu (🤖 Automatic · 🎮 Manual · Status · Pause · Withdraw)\n"
        "• /status — situation + *SELL buttons* for each position\n"
        "• /buy SYMBOL [%] — manual buy (e.g. `/buy CAKE` or `/buy FLOKI 100` for all-in)\n"
        "• /pausar (or /parar, /stop) — pauses the agent (resume with /start)\n"
        "• /panic — sells everything *and halts* the agent (emergency)\n"
        "• /reiniciar — *unlocks* after /panic or a withdrawal and resumes trading\n"
        "• /dashboard — read-only panel link\n"
        "• /registrar — registers the wallet in the competition (run once before Jun 22)\n"
        "• /competicao — competition registration status\n"
        "• /ajuda — this list\n\n"
        "_To sell without halting, use the 🔴 buttons inside /status._"
    )

    async def cmd_help(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text(self._HELP, parse_mode=ParseMode.MARKDOWN)

    async def cmd_unknown(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_master(update):
            return
        await update.message.reply_text(
            "❓ I didn't understand that command. See /ajuda for the full list.\n\n" + self._HELP,
            parse_mode=ParseMode.MARKDOWN)

    # ── lifecycle ────────────────────────────────────────────────────────
    def build(self) -> Application:
        if not self._token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN missing in .env")
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
        # fallback: any unrecognized command/text -> guide the user
        app.add_handler(MessageHandler(filters.COMMAND | (filters.TEXT & ~filters.COMMAND),
                                       self.cmd_unknown))
        self._app = app
        return app

    async def start_polling(self) -> None:
        app = self._app or self.build()
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        self._log.info("Telegram bot polling.")
