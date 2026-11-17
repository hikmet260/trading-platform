from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

try:
    from pywebpush import WebPushException, webpush
except ImportError:
    webpush = None  # type: ignore

    class WebPushException(Exception):  # type: ignore
        def __init__(self, message: str = "", response=None):
            super().__init__(message)
            self.response = response
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.settings import settings
from app.storage.database import Database
from app.storage.models import (
    NotificationDeliveryRecord,
    NotificationEventRecord,
    PushSubscriptionRecord,
    TradeRecord,
)

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 12
DELIVERY_LEASE_SECONDS = 90


def webpush_available() -> bool:
    return webpush is not None


async def enqueue_notification(
    database: Database,
    *,
    tenant_id: str,
    kind: str,
    payload: dict,
    dedupe_key: str,
    telegram: bool = False,
    push: bool = True,
    target_user_id: str | None = None,
) -> tuple[str, int]:
    """Persist an event and its channel deliveries in one transaction."""
    async with database.sessions() as session:
        result = await enqueue_notification_in_session(
            session, tenant_id=tenant_id, kind=kind, payload=payload,
            dedupe_key=dedupe_key, telegram=telegram, push=push,
            target_user_id=target_user_id,
        )
        await session.commit()
        return result


async def enqueue_notification_in_session(
    session,
    *,
    tenant_id: str,
    kind: str,
    payload: dict,
    dedupe_key: str,
    telegram: bool = False,
    push: bool = True,
    target_user_id: str | None = None,
) -> tuple[str, int]:
    """Add an event and deliveries to the caller's database transaction."""
    now = datetime.now(timezone.utc)
    event_id = str(uuid4())
    inserted = await session.execute(
        pg_insert(NotificationEventRecord)
        .values(id=event_id, tenant_id=tenant_id, dedupe_key=dedupe_key,
                kind=kind, payload=payload, created_at=now)
        .on_conflict_do_nothing(index_elements=[NotificationEventRecord.dedupe_key])
        .returning(NotificationEventRecord.id)
    )
    if inserted.scalar_one_or_none() is None:
        existing = await session.scalar(select(NotificationEventRecord.id).where(
            NotificationEventRecord.dedupe_key == dedupe_key,
        ))
        return str(existing or ""), 0
    deliveries: list[NotificationDeliveryRecord] = []
    if telegram:
        deliveries.append(NotificationDeliveryRecord(
            id=str(uuid4()), event_id=event_id, tenant_id=tenant_id,
            channel="telegram", status="pending", next_attempt_at=now,
        ))
    if push:
        query = select(PushSubscriptionRecord).where(PushSubscriptionRecord.tenant_id == tenant_id)
        if target_user_id:
            query = query.where(PushSubscriptionRecord.user_id == target_user_id)
        subscriptions = list((await session.scalars(query)).all())
        deliveries.extend(NotificationDeliveryRecord(
            id=str(uuid4()), event_id=event_id, tenant_id=tenant_id,
            channel="webpush", subscription_id=item.id,
            status="pending", next_attempt_at=now,
        ) for item in subscriptions)
    session.add_all(deliveries)
    return event_id, len(deliveries)


async def deliver_one(database: Database, telegram) -> bool:
    """Claim and deliver one due item. Row locks and leases make workers restart-safe."""
    now = datetime.now(timezone.utc)
    async with database.sessions() as session:
        await session.execute(update(NotificationDeliveryRecord).where(
            NotificationDeliveryRecord.status == "processing",
            NotificationDeliveryRecord.lease_until <= now,
            NotificationDeliveryRecord.attempts >= MAX_ATTEMPTS,
        ).values(status="failed", lease_until=None,
                 last_error="Delivery worker stopped during the final attempt"))
        delivery = await session.scalar(
            select(NotificationDeliveryRecord)
            .where(
                (
                    (NotificationDeliveryRecord.status.in_(["pending", "retry"]))
                    & (NotificationDeliveryRecord.next_attempt_at <= now)
                )
                | (
                    (NotificationDeliveryRecord.status == "processing")
                    & (NotificationDeliveryRecord.lease_until <= now)
                    & (NotificationDeliveryRecord.attempts < MAX_ATTEMPTS)
                )
            )
            .order_by(NotificationDeliveryRecord.next_attempt_at, NotificationDeliveryRecord.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if delivery is None:
            return False
        delivery.status = "processing"
        delivery.attempts += 1
        delivery.lease_until = now + timedelta(seconds=DELIVERY_LEASE_SECONDS)
        event = await session.get(NotificationEventRecord, delivery.event_id)
        subscription = (
            await session.get(PushSubscriptionRecord, delivery.subscription_id)
            if delivery.subscription_id else None
        )
        if event is None or (delivery.channel == "webpush" and subscription is None):
            delivery.status = "failed"
            delivery.last_error = "Notification event or push subscription no longer exists"
            delivery.lease_until = None
            await session.commit()
            return True
        if event.kind == "trade_proposal":
            trade_id = str((event.payload.get("trade") or {}).get("id") or "")
            trade = await session.get(TradeRecord, trade_id) if trade_id else None
            if (trade is None or trade.status != "proposed"
                    or (trade.expires_at is not None and trade.expires_at <= now)):
                delivery.status = "cancelled"
                delivery.last_error = "Trade proposal is no longer pending"
                delivery.lease_until = None
                await session.commit()
                return True
        payload = dict(event.payload)
        payload.setdefault("tag", event.id)
        channel = delivery.channel
        delivery_id = delivery.id
        subscription_data = ({
            "id": subscription.id,
            "endpoint": subscription.endpoint,
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
        } if subscription else None)
        if channel == "telegram" and not telegram.enabled:
            delivery.status = "cancelled"
            delivery.last_error = "Telegram notifications were disabled before delivery"
            delivery.lease_until = None
            await session.commit()
            return True
        await session.commit()

    try:
        if channel == "telegram":
            await telegram.deliver_notification(payload)
        elif channel == "webpush":
            push_payload = {key: payload[key] for key in ("title", "body", "url", "tag") if key in payload}
            await asyncio.to_thread(_send_webpush, subscription_data, push_payload)
        else:
            raise ValueError(f"Unsupported notification channel: {channel}")
    except Exception as exc:
        status_code = _push_status_code(exc)
        async with database.sessions() as session:
            delivery = await session.get(NotificationDeliveryRecord, delivery_id)
            if delivery is None:
                return True
            delivery.last_error = f"{type(exc).__name__}: {exc}"[:500]
            delivery.lease_until = None
            if channel == "webpush" and delivery.subscription_id:
                subscription = await session.get(PushSubscriptionRecord, delivery.subscription_id)
                if subscription:
                    subscription.last_error = delivery.last_error
            permanent_push_error = channel == "webpush" and status_code in {404, 410}
            if permanent_push_error:
                delivery.status = "failed"
                if delivery.subscription_id:
                    subscription = await session.get(PushSubscriptionRecord, delivery.subscription_id)
                    if subscription:
                        await session.delete(subscription)
            elif delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = "failed"
            else:
                delivery.status = "retry"
                retry_after = getattr(exc, "retry_after", None)
                try:
                    provider_delay = int(retry_after) if retry_after else 0
                except (TypeError, ValueError):
                    provider_delay = 0
                delay = min(provider_delay, 3600) if provider_delay > 0 else min(2 ** min(delivery.attempts, 10), 3600)
                delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            await session.commit()
        logger.warning("Notification delivery %s failed (attempt %d): %s", delivery_id, delivery.attempts, exc)
        return True

    async with database.sessions() as session:
        delivery = await session.get(NotificationDeliveryRecord, delivery_id)
        if delivery:
            delivery.status = "delivered"
            delivery.delivered_at = datetime.now(timezone.utc)
            delivery.lease_until = None
            delivery.last_error = None
        if channel == "webpush" and subscription_data:
            subscription = await session.get(PushSubscriptionRecord, subscription_data["id"])
            if subscription:
                subscription.last_success_at = datetime.now(timezone.utc)
                subscription.last_error = None
        await session.commit()
    return True


def _send_webpush(subscription: dict | None, payload: dict) -> None:
    if webpush is None:
        raise RuntimeError("pywebpush package is not installed")
    if not settings.webpush_private_key or not settings.webpush_subject:
        raise RuntimeError("Web Push VAPID keys are not configured")
    if not subscription:
        raise RuntimeError("Web Push subscription is missing")
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    webpush(
        subscription_info={
            "endpoint": subscription["endpoint"],
            "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
        },
        data=body,
        vapid_private_key=settings.webpush_private_key,
        vapid_claims={"sub": settings.webpush_subject},
        ttl=60,
        timeout=15,
    )


def _push_status_code(exc: Exception) -> int | None:
    if not isinstance(exc, WebPushException) or exc.response is None:
        return None
    return getattr(exc, "status_code", None) or getattr(exc.response, "status_code", None)
