from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.risk.controls import NewsEvent
from app.settings import settings

logger = logging.getLogger(__name__)

COUNTRIES = "United States,Euro Area,United Kingdom,Japan,Switzerland,Canada,Australia,New Zealand"
CURRENCY_BY_COUNTRY = {
    "united states": "USD", "euro area": "EUR", "united kingdom": "GBP", "japan": "JPY",
    "switzerland": "CHF", "canada": "CAD", "australia": "AUD", "new zealand": "NZD",
}


def parse_news_events(raw: str) -> list[NewsEvent]:
    """Parse explicitly configured UTC macro events."""
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("NEWS_EVENTS_JSON must be valid JSON") from exc
    if not isinstance(values, list):
        raise ValueError("NEWS_EVENTS_JSON must be a JSON array")
    events: list[NewsEvent] = []
    for item in values:
        if not isinstance(item, dict) or not {"name", "occurs_at"}.issubset(item):
            raise ValueError("Each news event requires name and occurs_at")
        raw_time = str(item["occurs_at"])
        date_only = len(raw_time) <= 10
        at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        events.append(NewsEvent(name=str(item["name"]), occurs_at=at,
                                currency=str(item.get("currency", "ALL")).upper(),
                                impact=str(item.get("impact", "high")).lower(), date_only=date_only))
    return events


class EconomicCalendar:
    """Trading Economics high-importance calendar. Requires the operator's API key."""

    def __init__(self, api_key: str, poll_seconds: int = 900) -> None:
        self.api_key = api_key
        self.poll_seconds = max(60, poll_seconds)
        self.events = parse_news_events(settings.news_events_json)
        self.updated_at: datetime | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def fresh(self) -> bool:
        return self.updated_at is not None and datetime.now(timezone.utc) - self.updated_at <= timedelta(seconds=self.poll_seconds * 2)

    async def start(self) -> None:
        if self._task:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="economic-calendar-refresh")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def refresh(self) -> bool:
        if not self.api_key:
            self.last_error = "NEWS_CALENDAR_API_KEY is not configured"
            return False
        start = datetime.now(timezone.utc).date()
        end = start + timedelta(days=7)
        url = f"https://api.tradingeconomics.com/calendar/country/{COUNTRIES}/{start.isoformat()}/{end.isoformat()}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
                response = await client.get(url, params={"c": self.api_key, "importance": 3, "f": "json"})
                response.raise_for_status()
                rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("Calendar provider returned an invalid response")
            parsed: list[NewsEvent] = []
            for row in rows:
                if not isinstance(row, dict) or int(row.get("Importance") or 0) < 3:
                    continue
                date_text = str(row.get("Date") or "")
                try:
                    at = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
                    date_only = len(date_text) <= 10
                except ValueError:
                    continue
                country = str(row.get("Country") or "")
                parsed.append(NewsEvent(
                    name=str(row.get("Event") or "High-impact economic release"), occurs_at=at,
                    currency=CURRENCY_BY_COUNTRY.get(country.lower(), "ALL"), impact="high", date_only=date_only,
                ))
            self.events = parsed
            self.updated_at = datetime.now(timezone.utc)
            self.last_error = None
            return True
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            # Preserve the last verified snapshot, but signal staleness so required mode fails closed.
            self.last_error = type(exc).__name__
            logger.warning("Economic calendar refresh failed (%s)", self.last_error)
            return False

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.refresh()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    def status(self) -> dict:
        return {"provider": "trading_economics", "configured": bool(self.api_key),
                "fresh": self.fresh, "event_count": len(self.events),
                "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                "last_error": self.last_error}
