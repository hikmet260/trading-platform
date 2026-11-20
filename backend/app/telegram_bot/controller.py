from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from sqlalchemy import select

from app.settings import settings
from app.engine.chart_snapshot import render_candlestick_snapshot
from app.storage.cache import MarketCache
from app.storage.database import Database
from app.storage.models import TradeRecord
from app.notifications.service import enqueue_notification, enqueue_notification_in_session


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
        self.on_ai_message = None
        self.role = settings.telegram_role
        self.enabled = settings.telegram_enabled
        self.bot_token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.allowed_user_ids = set(settings.telegram_user_id_list)
        self.timezone = settings.telegram_timezone
        self._signature: tuple[object, ...] | None = None

    async def apply_config(
        self, *, enabled: bool, bot_token: str, chat_id: str,
        allowed_user_ids: str, timezone_name: str,
    ) -> None:
        """Apply durable tenant settings and (re)start the bot if needed.

        The process role stays environment-controlled.  The API process uses
        ``publisher`` and the dedicated worker uses ``worker``; only the worker
        may poll Telegram.  Enabled/token/chat/ACL/timezone are tenant settings.
        """
        configured_allowed = {
            value.strip() for value in allowed_user_ids.split(",") if value.strip()
        } or ({chat_id} if chat_id else set())
        signature = (enabled, bot_token, chat_id, tuple(sorted(configured_allowed)), timezone_name)
        if signature == self._signature:
            return
        was_running = self.state == "running"
        if was_running:
            await self.stop()
        self.enabled = enabled
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.allowed_user_ids = configured_allowed
        self.timezone = timezone_name or "UTC"
        try:
            if was_running or enabled:
                await self.start()
        except Exception:
            await self.stop()
            self.state = "error"
            raise
        self._signature = signature

    async def start(self) -> None:
        if not self.enabled:
            self.state = "disabled"
            return
        if self.role == "publisher":
            self.state = "publisher"
            return
        if not self.bot_token or not self.chat_id:
            self.state = "misconfigured"
            return
        self.application = (
            Application.builder()
            .token(self.bot_token)
            .connect_timeout(20)
            .read_timeout(20)
            .write_timeout(20)
            .pool_timeout(20)
            .get_updates_connect_timeout(20)
            .get_updates_read_timeout(40)
            .get_updates_write_timeout(20)
            .get_updates_pool_timeout(20)
            .build()
        )
        self.application.add_handler(CallbackQueryHandler(self._on_callback))
        self.application.add_handler(CommandHandler("start", self._on_start_command))
        self.application.add_handler(CommandHandler("kill", self._on_kill_command))
        self.application.add_handler(CommandHandler("resume", self._on_resume_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text_message))
        await self.application.initialize()
        await self.application.start()
        if self.application.updater is None:
            self.state = "misconfigured"
            return
        await self.application.updater.start_polling(drop_pending_updates=False)
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

    async def send_trade_alert(self, trade: dict, *, session=None) -> None:
        trade_id = str(trade["id"])
        parameters = {
            "tenant_id": str(trade.get("tenant_id") or settings.tenant_id),
            "kind": "trade_proposal",
            "payload": {
                "kind": "trade_proposal", "trade": trade,
                "title": "Trade proposal", "body": "A trade proposal needs your review.",
                "url": "/trades",
            },
            "dedupe_key": f"trade-proposal:{trade_id}",
            "telegram": self.enabled,
            "push": True,
        }
        if session is None:
            await enqueue_notification(self.database, **parameters)
        else:
            await enqueue_notification_in_session(session, **parameters)

    async def send_event(self, *, tenant_id: str, kind: str, title: str, body: str,
                         url: str, dedupe_key: str, telegram: bool = True,
                         target_user_id: str | None = None, push: bool = True,
                         session=None) -> int:
        parameters = {
            "tenant_id": tenant_id,
            "kind": kind,
            "payload": {"kind": kind, "title": title, "body": body, "url": url},
            "dedupe_key": dedupe_key,
            "telegram": telegram and self.enabled,
            "push": push,
            "target_user_id": target_user_id,
        }
        if session is None:
            _, deliveries = await enqueue_notification(self.database, **parameters)
        else:
            _, deliveries = await enqueue_notification_in_session(session, **parameters)
        return deliveries

    async def deliver_notification(self, notification: dict) -> None:
        if not self.application or not self.chat_id:
            raise RuntimeError("Telegram notification worker is not connected")
        if notification.get("kind") == "trade_proposal":
            await self._deliver_trade_alert(notification["trade"])
            return
        title = escape(str(notification.get("title") or "Trading platform alert"))
        body = escape(str(notification.get("body") or "You have a new notification."))
        await self.application.bot.send_message(
            chat_id=int(self.chat_id), text=f"<b>{title}</b>\n{body}", parse_mode="HTML",
        )

    async def _deliver_trade_alert(self, trade: dict) -> None:
        if not self.application or not self.chat_id:
            raise RuntimeError("Telegram notification worker is not connected")
        trade_id = str(trade["id"])
        live_mode = trade.get("mode") == "live"
        ttl = 900
        expires_at = trade.get("expires_at")
        if expires_at:
            try:
                expiration = datetime.fromisoformat(str(expires_at))
                if expiration.tzinfo is None:
                    expiration = expiration.replace(tzinfo=timezone.utc)
                ttl = max(1, min(86_400, int((expiration - datetime.now(timezone.utc)).total_seconds())))
            except ValueError:
                pass
        await self.cache.redis.set(f"telegram:pending:{trade_id}", json.dumps(trade), ex=ttl)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Approve live order" if live_mode else "Approve (paper)", callback_data=f"approve:{trade_id}"),
             InlineKeyboardButton("Skip", callback_data=f"skip:{trade_id}")],
            [InlineKeyboardButton("Emergency Kill-Switch", callback_data="kill")],
        ])
        tier_info = f"Tier: {trade['tier']}\n" if trade.get("tier") else ""
        unit = str(trade.get("quantity_unit") or "lots")
        text = (
            f"<b>Trade proposal — {'live order' if live_mode else 'paper mode'}</b>\n"
            f"{escape(str(trade['side']).upper())} {escape(str(trade['symbol']))}\n"
            f"{tier_info}"
            f"Entry: {float(trade['entry_price']):.8g}\n"
            f"Position size: {float(trade['quantity']):.8g} {escape(unit)}\n"
            f"Risk: {float(trade['risk_amount']):.2f} ({float(trade['risk_pct']):.3g}%)\n"
            f"SL: {float(trade['stop_loss']):.8g}  TP: {float(trade['take_profit']):.8g}"
        )
        if trade.get("candles"):
            chart = render_candlestick_snapshot(trade["candles"], str(trade["symbol"]), str(trade.get("timeframe", "")))
            await self.application.bot.send_photo(
                chat_id=int(self.chat_id), photo=chart, caption=text,
                parse_mode="HTML", reply_markup=keyboard,
            )
        else:
            await self.application.bot.send_message(
                chat_id=int(self.chat_id), text=text, parse_mode="HTML", reply_markup=keyboard,
            )

    def _authorized_ids(self) -> set[str]:
        return self.allowed_user_ids or ({self.chat_id} if self.chat_id else set())

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if (str(query.message.chat_id if query.message else "") != str(self.chat_id)
                or str(query.from_user.id) not in self._authorized_ids()):
            await query.answer("Not authorized", show_alert=True)
            return
        await query.answer()
        data = query.data or ""
        if data.startswith("strategy_approve:") or data.startswith("strategy_discard:"):
            action, _, draft_id = data.partition(":")
            await self._handle_strategy_decision(query, action, draft_id)
            return
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
            async with self.database.sessions() as session:
                trade = await session.scalar(select(TradeRecord).where(
                    TradeRecord.id == trade_id,
                    TradeRecord.tenant_id == settings.tenant_id,
                    TradeRecord.status == "proposed",
                ))
                if trade is None:
                    await self._edit_callback_message(query, "This trade proposal expired or was already handled")
                    return
                pending_trade = {
                    "tenant_id": trade.tenant_id,
                    "mode": trade.mode,
                }
        else:
            pending_trade = json.loads(pending)
        tenant_id = pending_trade.get("tenant_id")
        if self.on_decision and not await self.on_decision(trade_id, action == "approve", tenant_id):
            await self._edit_callback_message(query, "This trade proposal is no longer pending or risk validation rejected it")
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
        message = update.effective_message
        if chat is None or user is None:
            if message:
                await message.reply_text("Could not identify this Telegram chat or account. Send /start and try again.")
            return False
        if str(chat.id) != str(self.chat_id):
            if message:
                await message.reply_text(
                    "This chat isn’t the configured Telegram destination. "
                    "Set Target Chat ID to the Chat ID shown by /start in this conversation."
                )
            return False
        if str(user.id) not in self._authorized_ids():
            if message:
                await message.reply_text(
                    "Your Telegram account isn’t on the allowed list. "
                    "Add the Allowed User ID shown by /start in Settings → Telegram."
                )
            return False
        return True

    async def _on_start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        user = update.effective_user
        message = update.effective_message
        if chat is None or user is None or message is None:
            return
        await message.reply_text(
            "Telegram setup IDs\n"
            f"Target Chat ID: {chat.id}\n"
            f"Your Allowed User ID: {user.id}\n\n"
            "Enter the chat ID as Target Chat ID in the platform Settings. "
            "Add your user ID to Allowed User IDs so you can approve proposals. "
            "In a private chat, these IDs are usually the same."
        )

    async def _on_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if message is None or chat is None or user is None or not message.text:
            return
        if not self.on_ai_message:
            await message.reply_text("AI chat is unavailable. Check the Telegram worker status and AI settings.")
            return
        try:
            response_message = await message.reply_text("Got it. I’m checking with the AI…")
        except Exception:
            logger.exception("Telegram could not acknowledge an authorized text message")
            return
        try:
            result = await self.on_ai_message(
                tenant_id=settings.tenant_id,
                chat_id=str(chat.id),
                user_id=str(user.id),
                message=message.text.strip(),
            )
        except Exception:
            logger.exception("Telegram AI message failed for tenant %s", settings.tenant_id)
            await response_message.edit_text("I couldn’t process that message. Check the AI settings and try again.")
            return

        reply = str(result.get("reply") or "How can I help?")
        draft_id = result.get("strategy_draft_id")
        if not draft_id:
            await response_message.edit_text(reply[:4000])
            return

        prompt = str(result.get("strategy_prompt") or "")
        name = str(result.get("strategy_name") or "AI strategy")
        text = (
            f"{reply}\n\nStrategy draft: {name}\n{prompt}\n\n"
            "Approving saves and activates this strategy. Trades then follow your dashboard execution mode and risk controls. "
            "Semi-auto requires approval for each trade proposal; full-auto can place trades automatically after risk checks."
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve & Activate", callback_data=f"strategy_approve:{draft_id}"),
            InlineKeyboardButton("Discard", callback_data=f"strategy_discard:{draft_id}"),
        ]])
        await response_message.edit_text(text[:4000], reply_markup=keyboard)

    async def _handle_strategy_decision(self, query, action: str, draft_id: str) -> None:
        if action not in {"strategy_approve", "strategy_discard"} or not draft_id:
            await self._edit_callback_message(query, "Unknown strategy action")
            return
        key = f"telegram:strategy-draft:{draft_id}"
        if action == "strategy_discard":
            await self.cache.redis.delete(key)
            await self._edit_callback_message(query, "Strategy draft discarded")
            return

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
        while True:
            try:
                local_timezone = ZoneInfo(self.timezone)
            except Exception:
                local_timezone = ZoneInfo("UTC")
            now = datetime.now(local_timezone)
            next_run = datetime.combine(now.date(), time(hour=20), local_timezone)
            if next_run <= now:
                next_run += timedelta(days=1)
            next_run_utc = next_run.astimezone(timezone.utc)
            start_utc = datetime.combine(now.date(), time.min, local_timezone).astimezone(timezone.utc)
            await asyncio.sleep((next_run - now).total_seconds())
            while True:
                try:
                    async with self.database.sessions() as session:
                        result = await session.execute(select(TradeRecord).where(
                            TradeRecord.tenant_id == settings.tenant_id,
                            TradeRecord.closed_at >= start_utc, TradeRecord.closed_at < next_run_utc,
                        ))
                        trades = list(result.scalars())
                    pnl = sum(trade.pnl or 0.0 for trade in trades if trade.pnl is not None)
                    wins = sum(1 for trade in trades if (trade.pnl or 0.0) > 0)
                    await self.send_event(
                        tenant_id=settings.tenant_id, kind="daily_digest",
                        title="Daily trading digest",
                        body=f"Closed trades: {len(trades)} · Wins: {wins} · Realized P&L: {pnl:.2f}",
                        url="/trades", dedupe_key=f"daily-digest:{settings.tenant_id}:{now.date().isoformat()}",
                        push=False,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logging.exception("Could not queue the daily trading digest; retrying in 60 seconds")
                    await asyncio.sleep(60)
