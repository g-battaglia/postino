"""Webhook processing services: event normalization and auto-suppression.

Responsible for:
- Normalizing Resend event type strings to internal EmailSend statuses.
- Persisting WebhookEvent records.
- Updating EmailSend records via the campaigns service.
- Auto-suppressing subscribers on bounce and complaint events.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from apps.campaigns.services import update_from_webhook
from apps.consent.models import UnsubscribeEvent
from apps.subscribers.models import Subscriber

from .models import WebhookEvent

logger = logging.getLogger(__name__)

_RESEND_TYPE_MAP: dict[str, str] = {
    "email.delivered": "delivered",
    "email.opened": "opened",
    "email.clicked": "clicked",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "delivered": "delivered",
    "opened": "opened",
    "clicked": "clicked",
    "bounced": "bounced",
    "complained": "complained",
}

_SUPPRESSION_EVENTS = frozenset({"bounced", "complained"})

_SUPPRESSED_STATUSES = frozenset({"unsubscribed", "bounced", "complained", "deleted"})


def _extract_provider_message_id(payload: dict[str, Any]) -> str:
    """Extract the provider message ID from a Resend webhook payload.

    Resend places the ID in various locations depending on the event type.
    Checked keys in order: data.email_id, data.id, email_id, id.
    """
    data = payload.get("data", {})
    if isinstance(data, dict):
        for key in ("email_id", "id"):
            value = data.get(key, "")
            if value:
                return str(value)
    for key in ("email_id", "id"):
        value = payload.get(key, "")
        if value:
            return str(value)
    return ""


def _extract_email(payload: dict[str, Any]) -> str:
    """Extract the recipient email address from a Resend webhook payload.

    Checked keys in order: data.to, data.email, to, email.

    Handles list/tuple values (Resend sometimes sends a list of recipients)
    by returning the first non-empty string element.
    """
    data = payload.get("data", {})
    if isinstance(data, dict):
        for key in ("to", "email"):
            value = data.get(key, "")
            if not value:
                continue
            if isinstance(value, (list, tuple)):
                for item in value:
                    candidate = str(item).strip().lower()
                    if candidate:
                        return candidate
            else:
                return str(value).strip().lower()
    for key in ("to", "email"):
        value = payload.get(key, "")
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                candidate = str(item).strip().lower()
                if candidate:
                    return candidate
        else:
            return str(value).strip().lower()
    return ""


def _normalize_event_type(raw_type: str) -> str:
    """Map a Resend event type string to an internal status string.

    Returns the raw type unchanged if no mapping exists.
    """
    return _RESEND_TYPE_MAP.get(raw_type, raw_type)


def _suppress_subscriber(
    *,
    subscriber: Subscriber,
    event_type: str,
    email_send_id: int | None = None,
) -> None:
    """Suppress a subscriber and create an UnsubscribeEvent.

    Sets subscriber status to ``bounced`` or ``complained`` and creates
    a global (email_type=None) UnsubscribeEvent. This function is safe
    to call on already-suppressed subscribers — it will create the
    UnsubscribeEvent but skip the status update (enforced by the model).
    """
    if event_type == "bounced":
        new_status = Subscriber.Status.BOUNCED
        method = "webhook_bounce"
    elif event_type == "complained":
        new_status = Subscriber.Status.COMPLAINED
        method = "webhook_complaint"
    else:
        return

    email = subscriber.email

    if subscriber.status not in _SUPPRESSED_STATUSES:
        subscriber.status = new_status
        subscriber.save(update_fields=["status", "updated_at"])

    UnsubscribeEvent.objects.create(
        subscriber=subscriber,
        email=email,
        email_type=None,
        method=method,
    )

    from apps.campaigns.services import cancel_enrollments_for_subscriber

    cancel_enrollments_for_subscriber(subscriber)


@transaction.atomic
def process_resend_event(payload: dict[str, Any]) -> WebhookEvent:
    """Process a single Resend webhook event.

    Steps:
    1. Normalize the event type.
    2. Persist a WebhookEvent record.
    3. If the event maps to an EmailSend status, update the EmailSend.
    4. For bounce/complaint events, auto-suppress the subscriber.

    Returns the persisted WebhookEvent.
    """
    raw_type = payload.get("type", "")
    normalized_type = _normalize_event_type(raw_type)

    webhook_event = WebhookEvent.objects.create(
        provider="resend",
        event_type=normalized_type,
        payload=payload,
        processed=False,
    )

    if normalized_type not in _RESEND_TYPE_MAP.values():
        webhook_event.processed = True
        webhook_event.save(update_fields=["processed"])
        return webhook_event

    provider_message_id = _extract_provider_message_id(payload)
    email_send = None

    if provider_message_id:
        email_send = update_from_webhook(provider_message_id, normalized_type)

    if normalized_type in _SUPPRESSION_EVENTS:
        subscriber = None

        if email_send and email_send.subscriber_id:
            try:
                subscriber = Subscriber.objects.get(pk=email_send.subscriber_id)
            except Subscriber.DoesNotExist:
                pass

        if subscriber is None:
            email = _extract_email(payload)
            if email:
                try:
                    subscriber = Subscriber.objects.get(email=email)
                except Subscriber.DoesNotExist:
                    logger.warning(
                        "No subscriber found for suppression (email=%s)", email,
                    )

        if subscriber:
            _suppress_subscriber(
                subscriber=subscriber,
                event_type=normalized_type,
                email_send_id=email_send.pk if email_send else None,
            )

    webhook_event.processed = True
    webhook_event.save(update_fields=["processed"])
    return webhook_event
