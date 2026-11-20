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

        history.extend([
            {"role": "user", "content": message[:2500]},
            {"role": "assistant", "content": "Checking strategy requirements..."},
        ])
        await self.cache.redis.set(history_key, json.dumps(history[-12:]), ex=86_400)
        return {"reply": "Checking strategy requirements..."}

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
