import uuid
import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse
import httpx
from sqlalchemy import select, update
from models.database import async_session
from models.schemas import Notification, NotificationEndpoint, NotificationDelivery, NotificationPreference


def resolve_public_target(target: str) -> tuple[str, dict[str, str], dict]:
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("Notification target must be an HTTP(S) URL without userinfo")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443, 8443}:
        raise ValueError("Notification target port is not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM
        )}
    except socket.gaierror as exc:
        raise ValueError("Notification target cannot be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Notification target must resolve only to public addresses")
    address = sorted(addresses)[0]
    host_ip = f"[{address}]" if ":" in address else address
    netloc = f"{host_ip}:{port}"
    pinned = urlunparse((parsed.scheme, netloc, parsed.path or "/", parsed.params,
                         parsed.query, ""))
    host = parsed.hostname if port in {80, 443} else f"{parsed.hostname}:{port}"
    return pinned, {"Host": host}, {"sni_hostname": parsed.hostname}


async def publish_notification(event_type: str, payload: dict, user_id: str | None = None,
                               tenant_id: str | None = None) -> str:
    if tenant_id is None:
        from auth import get_tenant_id
        tenant_id = get_tenant_id()
    notification_id = str(uuid.uuid4())
    async with async_session() as session:
        item = Notification(id=notification_id, tenant_id=tenant_id, user_id=user_id, event_type=event_type,
                            title=event_type.replace(".", " ").title(), body=str(payload), payload=payload)
        session.add(item)
        endpoints = (await session.execute(select(NotificationEndpoint).where(
            NotificationEndpoint.tenant_id == tenant_id,
            NotificationEndpoint.enabled.is_(True)))).scalars().all()
        preferences = []
        if user_id:
            preferences = (await session.execute(select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.event_type.in_([event_type, "*"]),
            ))).scalars().all()
        for endpoint in endpoints:
            exact = next((p for p in preferences if p.channel == endpoint.kind and p.event_type == event_type), None)
            fallback = next((p for p in preferences if p.channel == endpoint.kind and p.event_type == "*"), None)
            if (exact or fallback) and not (exact or fallback).enabled:
                continue
            session.add(NotificationDelivery(id=str(uuid.uuid4()), tenant_id=tenant_id,
                                             notification_id=notification_id,
                                             endpoint_id=endpoint.id, status="pending"))
        await session.commit()
    return notification_id


async def deliver_pending(limit: int = 50) -> dict:
    now = datetime.now(timezone.utc)
    sent = failed = 0
    async with async_session() as session:
        await session.execute(update(NotificationDelivery).where(
            NotificationDelivery.status == "sending",
            NotificationDelivery.claimed_at < now - timedelta(minutes=5),
        ).values(status="retry", claimed_at=None, next_retry_at=now))
        candidate_ids = (await session.execute(
            select(NotificationDelivery.id)
            .where(NotificationDelivery.status.in_(["pending", "retry"]))
            .where((NotificationDelivery.next_retry_at.is_(None)) | (NotificationDelivery.next_retry_at <= now))
            .limit(limit)
        )).scalars().all()
        claimed_ids = []
        for delivery_id in candidate_ids:
            claimed = await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.status.in_(["pending", "retry"]),
            ).values(status="sending", claimed_at=now))
            if claimed.rowcount == 1:
                claimed_ids.append(delivery_id)
        await session.commit()
        rows = (await session.execute(
            select(NotificationDelivery, Notification, NotificationEndpoint)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .join(NotificationEndpoint, NotificationEndpoint.id == NotificationDelivery.endpoint_id)
            .where(NotificationDelivery.id.in_(claimed_ids))
        )).all() if claimed_ids else []
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            for delivery, note, endpoint in rows:
                try:
                    pinned, headers, extensions = resolve_public_target(endpoint.target)
                    response = await client.post(pinned, headers=headers, extensions=extensions, json={
                        "channel": endpoint.kind, "event_type": note.event_type,
                        "title": note.title, "body": note.body, "payload": note.payload,
                    })
                    response.raise_for_status()
                    delivery.status, delivery.last_error, delivery.claimed_at = "sent", None, None
                    sent += 1
                except Exception as exc:
                    delivery.attempts += 1
                    delivery.status = "failed" if delivery.attempts >= 5 else "retry"
                    delivery.next_retry_at = now + timedelta(seconds=min(3600, 2 ** delivery.attempts * 30))
                    delivery.last_error = str(exc)[:1000]
                    delivery.claimed_at = None
                    failed += 1
        await session.commit()
    return {"sent": sent, "failed": failed}
