"""Consent and suppression service functions for the sending pipeline.

Provides URL builders, RFC 8058 header construction, consent/suppression
checks, and the three unsubscribe processing paths (per-type, global, GDPR
deletion).
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction

from apps.consent.models import ConsentRecord, EmailType, UnsubscribeEvent
from apps.consent.tokens import generate_double_optin_token, generate_unsubscribe_token
from apps.subscribers.models import Subscriber

_SUPPRESSED_STATUSES = frozenset({"unsubscribed", "bounced", "complained", "deleted"})


def build_unsubscribe_url(
    subscriber: Subscriber,
    email_type: EmailType | None = None,
) -> str:
    """Return a signed unsubscribe URL for the given subscriber.

    When *email_type* is provided the token is scoped to that type and the
    URL includes ``type=<slug>`` so the unsubscribe view knows which scope
    to verify against without trying every email type.
    """
    slug = email_type.slug if email_type else None
    token = generate_unsubscribe_token(subscriber.id, slug)
    base = settings.POSTINO_BASE_URL.rstrip("/")
    params: dict[str, str] = {"token": token}
    if slug is not None:
        params["type"] = slug
    return f"{base}/unsubscribe/?{urlencode(params)}"


def build_preferences_url(subscriber: Subscriber) -> str:
    """Return a signed preference-center URL for the given subscriber."""
    token = generate_unsubscribe_token(subscriber.id)
    base = settings.POSTINO_BASE_URL.rstrip("/")
    return f"{base}/preferences/?{urlencode({'token': token})}"


def build_unsubscribe_headers(
    subscriber: Subscriber,
    email_type: EmailType | None = None,
) -> dict[str, str]:
    """Return RFC 8058 one-click unsubscribe headers.

    Every outgoing email **must** include these headers. The email backend
    validates their presence before handing off to the provider.

    The ``List-Unsubscribe`` URL points to the dedicated one-click endpoint
    (``/unsubscribe/one-click/``) with an **unscoped** token that triggers a
    global unsubscribe when POSTed by the mail provider.
    """
    token = generate_unsubscribe_token(subscriber.id)
    base = settings.POSTINO_BASE_URL.rstrip("/")
    one_click_url = f"{base}/unsubscribe/one-click/?{urlencode({'token': token})}"
    return {
        "List-Unsubscribe": f"<{one_click_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def get_latest_consent_action(
    subscriber: Subscriber,
    email_type: EmailType | None,
) -> str | None:
    """Return the action string of the most recent consent record, or None."""
    qs = ConsentRecord.objects.filter(subscriber=subscriber)
    if email_type is not None:
        qs = qs.filter(email_type=email_type)
    else:
        qs = qs.filter(email_type__isnull=True)
    record = qs.order_by("-created_at", "-pk").values_list("action", flat=True).first()
    return record


def has_marketing_consent(subscriber: Subscriber, email_type: EmailType) -> bool:
    """True when the latest consent record for this type is a grant."""
    return get_latest_consent_action(subscriber, email_type) == ConsentRecord.Action.GRANT


def is_email_suppressed(email: str, email_type: EmailType | None = None) -> bool:
    """True when an UnsubscribeEvent blocks the given email address.

    A global event (``email_type IS NULL``) blocks all email. A per-type
    event blocks only that type. Email comparison is case-insensitive.
    """
    normalized = email.strip().lower()
    qs = UnsubscribeEvent.objects.filter(email=normalized)
    if email_type is not None:
        qs = qs.filter(email_type=email_type)
    else:
        qs = qs.filter(email_type__isnull=True)
    if qs.exists():
        return True
    if email_type is not None:
        return UnsubscribeEvent.objects.filter(
            email=normalized,
            email_type__isnull=True,
        ).exists()
    return False


def can_send_to_subscriber(subscriber: Subscriber, email_type: EmailType) -> bool:
    """Check all suppression and consent rules before sending.

    Rules enforced (in order):

    1. Subscriber status must be ``active``.
    2. No global UnsubscribeEvent for this email address.
    3. No per-type UnsubscribeEvent for this email type.
    4. Transactional email types bypass marketing consent.
    5. Non-transactional email requires the latest consent record to be a grant.
    """
    if subscriber.status != Subscriber.Status.ACTIVE:
        return False

    normalized = subscriber.email.strip().lower()

    global_suppressed = UnsubscribeEvent.objects.filter(
        email=normalized,
        email_type__isnull=True,
    ).exists()
    if global_suppressed:
        return False

    type_suppressed = UnsubscribeEvent.objects.filter(
        email=normalized,
        email_type=email_type,
    ).exists()
    if type_suppressed:
        return False

    if email_type.is_transactional:
        return True

    return has_marketing_consent(subscriber, email_type)


# ---------------------------------------------------------------------------
# Unsubscribe processing (called synchronously from views)
# ---------------------------------------------------------------------------


def process_per_type_unsubscribe(
    subscriber: Subscriber,
    email_type: EmailType,
    *,
    ip_address: str | None = None,
    user_agent: str = "",
    method: str = "link",
) -> UnsubscribeEvent:
    """Withdraw consent for a single email type.

    Creates a ConsentRecord withdraw and an UnsubscribeEvent. Does NOT
    change the subscriber's global status. Wrapped in ``atomic()`` so
    both writes succeed or roll back together.
    """
    with transaction.atomic():
        ConsentRecord.objects.create(
            subscriber=subscriber,
            email_type=email_type,
            action=ConsentRecord.Action.WITHDRAW,
            method=method,
            ip_address=ip_address,
        )
        return UnsubscribeEvent.objects.create(
            subscriber=subscriber,
            email=subscriber.email,
            email_type=email_type,
            method=method,
            ip_address=ip_address,
            user_agent=user_agent,
        )


def process_global_unsubscribe(
    subscriber: Subscriber,
    *,
    ip_address: str | None = None,
    user_agent: str = "",
    method: str = "link",
) -> UnsubscribeEvent:
    """Set subscriber status to unsubscribed and create an audit event.

    Idempotent: if the subscriber is already in a suppressed status, a new
    UnsubscribeEvent is still created but the status is not changed.
    Wrapped in ``atomic()`` so status change and event creation are atomic.
    """
    with transaction.atomic():
        if subscriber.status not in _SUPPRESSED_STATUSES:
            subscriber.status = Subscriber.Status.UNSUBSCRIBED
            subscriber.save(update_fields=["status", "updated_at"])

        event = UnsubscribeEvent.objects.create(
            subscriber=subscriber,
            email=subscriber.email,
            email_type=None,
            method=method,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        from apps.campaigns.services import cancel_enrollments_for_subscriber

        cancel_enrollments_for_subscriber(subscriber)

        return event


def process_gdpr_deletion(
    subscriber: Subscriber,
    *,
    ip_address: str | None = None,
    user_agent: str = "",
    method: str = "gdpr_deletion",
) -> UnsubscribeEvent:
    """GDPR Art. 17 erasure: suppress and purge personal data.

    Sets status to ``deleted``, blanks personal fields, clears tags.
    An UnsubscribeEvent is created preserving the original email for
    compliance audit. ConsentRecord and UnsubscribeEvent rows are never
    deleted. Wrapped in ``atomic()`` so event creation and field purge
    succeed or roll back together.

    Idempotent: if the subscriber is already deleted, a new event is
    still created but no further field purging occurs.
    """
    with transaction.atomic():
        event = UnsubscribeEvent.objects.create(
            subscriber=subscriber,
            email=subscriber.email,
            email_type=None,
            method=method,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        if subscriber.status != Subscriber.Status.DELETED:
            subscriber.status = Subscriber.Status.DELETED
            subscriber.name = ""
            subscriber.metadata = {}
            subscriber.ip_address = None
            subscriber.source_id = ""
            subscriber.double_optin_token = None
            subscriber.double_optin_confirmed_at = None
            subscriber.tags.clear()
            subscriber.save(update_fields=[
                "status", "name", "metadata", "ip_address", "source_id",
                "double_optin_token", "double_optin_confirmed_at", "updated_at",
            ])

        from apps.campaigns.services import cancel_enrollments_for_subscriber

        cancel_enrollments_for_subscriber(subscriber)

        return event


# ---------------------------------------------------------------------------
# Double opt-in confirmation
# ---------------------------------------------------------------------------


class DoubleOptinError(Exception):
    """Raised when double opt-in cannot be initiated or confirmed."""


def initiate_double_optin(subscriber: Subscriber) -> str:
    """Generate a double opt-in token and return the confirmation URL.

    The subscriber must be in PENDING status.  The token is saved to the
    subscriber so that the confirmation flow can verify it was issued.

    Returns
    -------
    str
        The full confirmation URL the subscriber must visit.

    Raises
    ------
    DoubleOptinError
        If the subscriber is not in PENDING status.
    """
    if subscriber.status != Subscriber.Status.PENDING:
        raise DoubleOptinError(
            "Double opt-in can only be initiated for pending subscribers."
        )

    token = generate_double_optin_token(subscriber.id)
    subscriber.double_optin_token = token
    subscriber.save(update_fields=["double_optin_token", "updated_at"])

    base = settings.POSTINO_BASE_URL.rstrip("/")
    return f"{base}/confirm/?{urlencode({'token': token})}"


def confirm_double_optin(subscriber: Subscriber) -> None:
    """Transition subscriber from PENDING to ACTIVE and record consent.

    Idempotent: if the subscriber is already ACTIVE the function returns
    without error and without creating duplicate consent records.

    Raises
    ------
    DoubleOptinError
        If the subscriber is in a suppressed status.
    """
    if subscriber.status == Subscriber.Status.ACTIVE:
        if subscriber.double_optin_token:
            subscriber.double_optin_token = None
            subscriber.save(update_fields=["double_optin_token", "updated_at"])
        return

    if subscriber.status in _SUPPRESSED_STATUSES:
        raise DoubleOptinError(
            "Cannot confirm a suppressed subscriber."
        )

    with transaction.atomic():
        subscriber.status = Subscriber.Status.ACTIVE
        subscriber.double_optin_token = None
        subscriber.double_optin_confirmed_at = _now()
        subscriber.save(update_fields=[
            "status", "double_optin_token", "double_optin_confirmed_at",
            "updated_at",
        ])

        ConsentRecord.objects.create(
            subscriber=subscriber,
            email_type=None,
            action=ConsentRecord.Action.GRANT,
            method="double_optin",
        )

    from apps.campaigns.services import trigger_sequences_for_subscriber_created

    trigger_sequences_for_subscriber_created(subscriber)


def _now():
    """Return the current timezone-aware datetime.

    Wrapped in a function so tests can mock it if needed.
    """
    from django.utils import timezone
    return timezone.now()
