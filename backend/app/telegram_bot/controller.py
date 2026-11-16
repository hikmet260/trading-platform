from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from sqlalchemy import select

from app.settings import settings
from app.engine.chart_snapshot import render_candlestick_snapshot
from app.storage.cache import MarketCache
from app.storage.database import Database
from app.storage.models import TradeRecord


class TelegramController:
    def __init__(self, cache: MarketCache, database: Database) -> None:
        self.cache = cache
        self.database = database
        self.application: Application | None = None
        self.digest_task: asyncio.Task | None = None
        self.state = "disabled"
        self.on_kill = None
        self.on_resume = None
        self.on_decision = None

    async def start(self) -> None:
        if not settings.telegram_enabled:
            self.state = "disabled"
            return
        if settings.telegram_role == "publisher":
            self.state = "publisher"
            return
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            self.state = "misconfigured"
            return
        self.application = Application.builder().token(settings.telegram_bot_token).build()
        self.application.add_handler(CallbackQueryHandler(self._on_callback))
        self.application.add_handler(CommandHandler("kill", self._on_kill_command))
        self.application.add_handler(CommandHandler("resume", self._on_resume_command))
        await self.application.initialize()
        await self.application.start()
        if self.application.updater is None:
            self.state = "misconfigured"
            return
        await self.application.updater.start_polling(drop_pending_updates=True)
        self.digest_task = asyncio.create_task(self._digest_loop(), name="telegram-daily-digest")
        self.state = "running"

    async def stop(self) -> None:
        if self.digest_task:
            self.digest_task.cancel()
            await asyncio.gather(self.digest_task, return_exceptions=True)
            self.digest_task = None
        if self.application:
            if self.application.updater and self.application.updater.running:
                await self.application.updater.stop()
            if self.application.running:
                await self.application.stop()
            await self.application.shutdown()
            self.application = None
        if self.state == "running":
            self.state = "stopped"

    async def send_trade_alert(self, trade: dict) -> None:
        if not self.application:
            if settings.telegram_enabled and settings.telegram_role == "publisher":
                await self.cache.redis.publish("telegram:alerts", json.dumps(trade, allow_nan=False))
            return
        trade_id = str(trade["id"])
        live_mode = trade.get("mode") == "live"
        await self.cache.redis.set(f"telegram:pending:{trade_id}", json.dumps(trade), ex=900)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Approve live order" if live_mode else "Approve (paper)", callback_data=f"approve:{trade_id}"),
             InlineKeyboardButton("Skip", callback_data=f"skip:{trade_id}")],
            [InlineKeyboardButton("Emergency Kill-Switch", callback_data="kill")],
        ])
        text = (
            f"<b>Trade proposal — {'live order' if live_mode else 'paper mode'}</b>\n"
            f"{escape(str(trade['side']).upper())} {escape(str(trade['symbol']))}\n"
            f"Entry: {float(trade['entry_price']):.8g}\n"
            f"Position size: {float(trade['quantity']):.8g}\n"
            f"Risk: {float(trade['risk_amount']):.2f} ({float(trade['risk_pct']):.3g}%)\n"
            f"SL: {float(trade['stop_loss']):.8g}  TP: {float(trade['take_profit']):.8g}"
        )
        if trade.get("candles"):
            chart = render_candlestick_snapshot(trade["candles"], str(trade["symbol"]), str(trade.get("timeframe", "")))
            await self.application.bot.send_photo(
                chat_id=int(settings.telegram_chat_id), photo=chart, caption=text,
                parse_mode="HTML", reply_markup=keyboard,
            )
        else:
            await self.application.bot.send_message(
                chat_id=int(settings.telegram_chat_id), text=text, parse_mode="HTML", reply_markup=keyboard,
            )

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if (str(query.message.chat_id if query.message else "") != str(settings.telegram_chat_id)
                or str(query.from_user.id) not in settings.telegram_user_id_list):
            await query.answer("Not authorized", show_alert=True)
            return
        await query.answer()
        data = query.data or ""
        if data == "kill":
            result = await self.on_kill() if self.on_kill else 0
            await self._edit_callback_message(query,
                self._kill_message(result),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Resume paper signals", callback_data="resume")]]),
            )
            return
        if data.startswith("resume"):
            if self.on_resume:
                resumed = await self.on_resume()
                if resumed is False:
                    await self._edit_callback_message(query, "Cannot resume: live positions remain open or unreconciled")
                    return
            else:
                await self.cache.set_kill_switch(False)
            await self._edit_callback_message(query, "Kill-switch cleared. Paper mode remains active.")
            return
        action, _, trade_id = data.partition(":")
        if action not in {"approve", "skip"} or not trade_id:
            await self._edit_callback_message(query, "Unknown action")
            return
        pending = await self.cache.redis.get(f"telegram:pending:{trade_id}")
        if not pending:
            await self._edit_callback_message(query, "This trade proposal expired")
            return
        pending_trade = json.loads(pending)
        tenant_id = pending_trade.get("tenant_id")
        if self.on_decision and not await self.on_decision(trade_id, action == "approve", tenant_id):
            await self._edit_callback_message(query, "This paper trade is no longer pending or risk validation rejected it")
            return
        await self.cache.redis.set(f"telegram:decision:{trade_id}", action, ex=86_400)
        await self.cache.redis.delete(f"telegram:pending:{trade_id}")
        if action == "approve":
            response = "Protected live order submitted" if pending_trade.get("mode") == "live" else "Approved for paper simulation only"
        else:
            response = "Trade proposal skipped"
        await self._edit_callback_message(query, response)

    @staticmethod
    async def _edit_callback_message(query, text: str, reply_markup=None) -> None:
        if query.message and query.message.photo:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text, reply_markup=reply_markup)

    async def _authorized(self, update: Update) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        if (chat is None or str(chat.id) != str(settings.telegram_chat_id) or user is None
                or str(user.id) not in settings.telegram_user_id_list):
            if update.effective_message:
                await update.effective_message.reply_text("Not authorized")
            return False
        return True

    async def _on_kill_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        result = await self.on_kill() if self.on_kill else 0
        if update.effective_message:
            await update.effective_message.reply_text(self._kill_message(result))

    @staticmethod
    def _kill_message(result) -> str:
        if isinstance(result, dict):
            paper = result.get("closed_paper_positions", 0)
            live = result.get("closed_live_positions", 0)
            remaining = result.get("unclosed_live_positions", 0)
            return (f"Emergency kill-switch active. Closed {paper} paper and {live} live position(s). "
                    f"Live positions still requiring broker confirmation: {remaining}. "
                    f"Failure count: {len(result.get('close_errors', []))}.")
        return f"Emergency kill-switch active. Closed {result} paper position(s)."

    async def _on_resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        if self.on_resume:
            resumed = await self.on_resume()
            if resumed is False:
                if update.effective_message:
                    await update.effective_message.reply_text("Cannot resume: live positions remain open or unreconciled")
                return
        else:
            await self.cache.set_kill_switch(False)
        if update.effective_message:
            await update.effective_message.reply_text("Kill-switch cleared. Paper mode remains active.")

    async def _digest_loop(self) -> None:
        local_timezone = ZoneInfo(settings.telegram_timezone)
        while True:
            now = datetime.now(local_timezone)
            next_run = datetime.combine(now.date(), time(hour=20), local_timezone)
            if next_run <= now:
                next_run += timedelta(days=1)
            next_run_utc = next_run.astimezone(timezone.utc)
            start_utc = datetime.combine(now.date(), time.min, local_timezone).astimezone(timezone.utc)
            await asyncio.sleep((next_run - now).total_seconds())
            async with self.database.sessions() as session:
                result = await session.execute(select(TradeRecord).where(
                    TradeRecord.tenant_id == settings.tenant_id,
                    TradeRecord.closed_at >= start_utc, TradeRecord.closed_at < next_run_utc,
                ))
                trades = list(result.scalars())
            pnl = sum(trade.pnl or 0.0 for trade in trades)
            wins = sum(1 for trade in trades if (trade.pnl or 0.0) > 0)
            if self.application:
                await self.application.bot.send_message(
                    chat_id=int(settings.telegram_chat_id),
                    text=f"Daily trading digest\nClosed trades: {len(trades)}\nWins: {wins}\nRealized P&L: {pnl:.2f}",
                )
