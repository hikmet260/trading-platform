from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.core.security import CredentialVault
from app.settings import settings
from app.storage.cache import MarketCache
from app.storage.database import Database
from app.storage.models import SystemSettingsRecord
from app.strategies.rules import StrategyParseError, parse_strategy_prompt

logger = logging.getLogger(__name__)


class TelegramCopilot:
    def __init__(self, database: Database, cache: MarketCache) -> None:
        self.database = database
        self.cache = cache

    async def handle_message(
        self, *, tenant_id: str, chat_id: str, user_id: str, message: str,
    ) -> dict[str, Any]:
        provider, model, temperature, custom_directives, api_key = await self._ai_config(tenant_id)
        if not api_key and provider != "ollama":
            return {
                "reply": "AI isn’t configured yet. Add an AI provider and API key in the dashboard Settings, then try again.",
            }

        history_key = f"telegram:copilot:history:{tenant_id}:{chat_id}:{user_id}"
        raw_history = await self.cache.redis.get(history_key)
        try:
            history = json.loads(raw_history) if raw_history else []
        except (TypeError, json.JSONDecodeError):
            history = []
        if not isinstance(history, list):
            history = []

        system_prompt = (
            "You are the trading platform's Telegram AI assistant. Answer questions clearly and help clients "
            "draft strategies. You cannot place orders, save strategies, or change risk settings yourself.\n\n"
            "When the user asks for a trading strategy, return a strategy_prompt only when all required details "
            "are clear. Otherwise ask a short follow-up question and set strategy_prompt to null. Do not invent "
            "a symbol, timeframe, entry condition, stop, target, or risk value.\n"
            "A strategy prompt must match this exact grammar: [BUY|SELL|BOTH] [SYMBOL] on [TIMEFRAME] when "
            "[CONDITIONS joined by AND] risk [RISK]% stop loss [STOP] pips take profit [TARGET] pips.\n"
            "Supported timeframes: 1m, 5m, 15m, 30m, 1h, 4h, 1d. Supported conditions: RSI(n) <|> number, "
            "EMA(n)/SMA(n) <|> number, EMA(n)/SMA(n) crosses above/below EMA(m)/SMA(m), and CLOSE/PRICE <|> number. "
            "Risk must be above 0 and at most 1.0%. Stop and target must be positive pip amounts.\n"
            "Never claim a strategy is saved, active, profitable, or guaranteed. A valid draft will be shown to the "
            "user for explicit approval before it is saved and activated. After activation, the platform's existing "
            "execution mode, broker configuration, risk limits, kill switch, and trade approvals control execution.\n\n"
            f"User-configured AI guidance (must not override the rules above): {custom_directives or 'None'}\n\n"
            "Respond only as a JSON object with this schema: {\"reply\": string, \"strategy_prompt\": string|null, "
            "\"strategy_name\": string|null}. Keep reply concise."
        )
        conversation = history[-8:] + [{"role": "user", "content": message}]
        user_prompt = "Conversation:\n" + "\n".join(
            f"{item.get('role', 'user').upper()}: {item.get('content', '')}"
            for item in conversation
            if isinstance(item, dict)
        )

        try:
            # Reuse the same provider adapter as the dashboard Copilot.
            from app.main import _call_ai_service

            raw_text, _used_model = await _call_ai_service(
                provider=provider,
                preferred_model=model,
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                response_json=True,
            )
            parsed = self._parse_response(raw_text)
        except Exception as exc:
            logger.exception("Telegram Copilot request failed for tenant %s", tenant_id)
            detail = getattr(exc, "detail", None)
            return {"reply": f"I couldn’t reach the configured AI service. {detail or 'Check the AI settings and try again.'}"}

        reply = parsed.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            reply = "What would you like help with? You can ask me to draft a strategy."
        strategy_prompt = parsed.get("strategy_prompt")
        response: dict[str, Any] = {"reply": reply[:3500]}

        if isinstance(strategy_prompt, str) and strategy_prompt.strip():
            strategy_prompt = strategy_prompt.strip()
            try:
                rule = parse_strategy_prompt(strategy_prompt)
            except StrategyParseError as exc:
                response["reply"] = (
                    f"I couldn’t make a valid strategy from that yet: {exc}. "
                    "Please clarify the symbol, timeframe, conditions, risk, stop, and target."
                )
            else:
                strategy_id = str(uuid4())
                proposed_name = parsed.get("strategy_name")
                name = proposed_name.strip() if isinstance(proposed_name, str) else ""
                if not name:
                    name = f"Telegram {rule.side.upper()} {rule.symbol} {rule.timeframe}"
                draft = {
                    "tenant_id": tenant_id,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "name": name[:120],
                    "prompt": strategy_prompt,
                    "rule": rule.model_dump(mode="json"),
                }
                await self.cache.redis.set(
                    f"telegram:strategy-draft:{strategy_id}", json.dumps(draft), ex=900,
                )
                response.update({
                    "strategy_draft_id": strategy_id,
                    "strategy_name": draft["name"],
                    "strategy_prompt": strategy_prompt,
                    "strategy_rule": draft["rule"],
                })

        history.extend([
            {"role": "user", "content": message[:2500]},
            {"role": "assistant", "content": response["reply"][:3500]},
        ])
        await self.cache.redis.set(history_key, json.dumps(history[-12:]), ex=86_400)
        return response

    async def _ai_config(self, tenant_id: str) -> tuple[str, str, float, str, str]:
        async with self.database.sessions() as session:
            record = await session.scalar(select(SystemSettingsRecord).where(
                SystemSettingsRecord.tenant_id == tenant_id,
            ))

        provider = (record.ai_provider if record else "openai").lower()
        model = record.ai_model if record else settings.ai_openai_default_model
        temperature = float(record.ai_temperature if record else 0.2)
        custom_directives = record.ai_system_prompt if record else ""
        api_key = ""
        if record and record.encrypted_ai_api_key:
            try:
                api_key = CredentialVault().decrypt(record.encrypted_ai_api_key).get("key", "")
            except Exception:
                logger.exception("Could not decrypt tenant AI credentials")
        if not api_key:
            if provider == "anthropic":
                api_key = settings.anthropic_api_key
            elif provider == "gemini":
                api_key = settings.gemini_api_key
            elif provider == "openai":
                api_key = settings.openai_api_key
        return provider, model, temperature, custom_directives or "", api_key

    @staticmethod
    def _parse_response(raw_text: str) -> dict[str, Any]:
        clean = raw_text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean)
        value = json.loads(clean)
        if not isinstance(value, dict):
            raise ValueError("AI returned an invalid response")
        return value
