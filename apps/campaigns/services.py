"""Campaign, email-send, and sequence services.

Thin helpers for creating EmailSend log entries and advancing their
status, the full campaign sending pipeline, and the complete sequence
evaluation engine with enrollment, triggers, auto-cancel, and GDPR checks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.consent.services import (
    build_unsubscribe_headers,
    build_unsubscribe_url,
    can_send_to_subscriber,
)
from apps.core.email_backend import get_backend
from apps.subscribers.models import Subscriber
from apps.templates_mgr.renderer import render_saved_template

from .models import Campaign, EmailSend, Sequence, SequenceEnrollment, SequenceStep

if TYPE_CHECKING:
    from apps.consent.models import EmailType

logger = logging.getLogger(__name__)


def create_email_send(
    *,
    subscriber: Subscriber,
    campaign: Campaign | None = None,
    sequence_step: SequenceStep | None = None,
    email_type: EmailType,
    subject_line: str,
) -> EmailSend:
    """Create an EmailSend record in ``queued`` status."""
    return EmailSend.objects.create(
        subscriber=subscriber,
        campaign=campaign,
        sequence_step=sequence_step,
        email_type=email_type,
        subject_line_used=subject_line,
    )


def mark_sent(email_send: EmailSend, *, provider_message_id: str = "") -> EmailSend:
    """Transition an EmailSend from ``queued`` to ``sent``."""
    email_send.status = EmailSend.Status.SENT
    email_send.provider_message_id = provider_message_id
    email_send.sent_at = timezone.now()
    email_send.save(update_fields=["status", "provider_message_id", "sent_at"])
    return email_send


def mark_failed(email_send: EmailSend, *, error_message: str = "") -> EmailSend:
    """Transition an EmailSend to ``failed`` with an optional error message."""
    email_send.status = EmailSend.Status.FAILED
    email_send.error_message = error_message
    email_send.save(update_fields=["status", "error_message"])
    return email_send


def mark_delivered(email_send: EmailSend) -> EmailSend:
    """Transition an EmailSend to ``delivered``."""
    email_send.status = EmailSend.Status.DELIVERED
    email_send.delivered_at = timezone.now()
    email_send.save(update_fields=["status", "delivered_at"])
    return email_send


@transaction.atomic
def update_from_webhook(
    provider_message_id: str,
    event_type: str,
) -> EmailSend | None:
    """Update EmailSend status from a provider webhook event.

    Maps provider event types to EmailSend status transitions and sets
    the corresponding timestamp. Returns the updated EmailSend or None
    if no matching record is found.
    """
    try:
        email_send = EmailSend.objects.get(provider_message_id=provider_message_id)
    except EmailSend.DoesNotExist:
        return None

    now = timezone.now()
    event_map: dict[str, tuple[str, str]] = {
        "delivered": ("status", "delivered_at"),
        "opened": ("status", "opened_at"),
        "clicked": ("status", "clicked_at"),
        "bounced": ("status", "bounced_at"),
        "complained": ("status", "complained_at"),
    }

    mapping = event_map.get(event_type)
    if mapping is None:
        return email_send

    status_field, timestamp_field = mapping
    status_value = event_type

    # Only advance, never regress status
    status_order = list(EmailSend.Status.values)
    current_idx = status_order.index(email_send.status) if email_send.status in status_order else -1
    new_idx = status_order.index(status_value) if status_value in status_order else -1

    if new_idx > current_idx:
        setattr(email_send, status_field, status_value)
        setattr(email_send, timestamp_field, now)
        email_send.save(update_fields=[status_field, timestamp_field])

    return email_send


# ---------------------------------------------------------------------------
# Campaign sending pipeline
# ---------------------------------------------------------------------------


@dataclass
class SendResult:
    """Summary of a campaign send operation."""

    campaign_id: int
    campaign_name: str
    eligible: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class CampaignSendError(Exception):
    """Raised when a campaign cannot be sent."""


def _build_audience_queryset(campaign: Campaign) -> tuple[list[Subscriber], int]:
    """Build the subscriber queryset from campaign audience_filter.

    Supported filter keys:
    - ``tags``: list of tag names (subscriber must have at least one).
    - ``status``: subscriber status string (default: ``active``).
    - ``health_below``: integer, only subscribers with health_score < value.
    - ``health_above``: integer, only subscribers with health_score >= value.

    Returns (subscribers list, total count before consent checks).
    """
    filt = campaign.audience_filter or {}
    qs = Subscriber.objects.all()

    status = filt.get("status", Subscriber.Status.ACTIVE)
    qs = qs.filter(status=status)

    tags = filt.get("tags")
    if tags:
        if isinstance(tags, str):
            tags = [tags]
        qs = qs.filter(tags__name__in=tags).distinct()

    health_below = filt.get("health_below")
    if health_below is not None:
        qs = qs.filter(health_score__lt=int(health_below))

    health_above = filt.get("health_above")
    if health_above is not None:
        qs = qs.filter(health_score__gte=int(health_above))

    total = qs.count()
    subscribers = list(qs)
    return subscribers, total


def send_campaign(campaign_id: int) -> SendResult:
    """Send a campaign to its full eligible audience.

    Steps:
    1. Load campaign, template, email_type.
    2. Query audience from ``audience_filter``.
    3. For each candidate, verify consent via ``can_send_to_subscriber``.
    4. Render template with subscriber context.
    5. Send via configured email backend.
    6. Create EmailSend records and update campaign status.

    Returns a :class:`SendResult` summary.
    """
    try:
        campaign = Campaign.objects.select_related("email_type", "template").get(
            pk=campaign_id,
        )
    except Campaign.DoesNotExist:
        raise CampaignSendError(f"Campaign {campaign_id} not found.") from None

    if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.SCHEDULED):
        raise CampaignSendError(
            f"Campaign '{campaign.name}' is {campaign.get_status_display()}, "
            f"cannot send. Must be draft or scheduled."
        )

    email_type = campaign.email_type
    template = campaign.template

    audience, _audience_total = _build_audience_queryset(campaign)
    backend = get_backend()

    campaign.status = Campaign.Status.SENDING
    campaign.save(update_fields=["status", "updated_at"])

    result = SendResult(
        campaign_id=campaign.pk,
        campaign_name=campaign.name,
    )

    for subscriber in audience:
        if not can_send_to_subscriber(subscriber, email_type):
            result.skipped += 1
            continue

        result.eligible += 1

        try:
            unsubscribe_url = build_unsubscribe_url(subscriber, email_type)
            context = {
                "subscriber_name": subscriber.name,
                "subscriber_email": subscriber.email,
                "unsubscribe_url": unsubscribe_url,
            }

            from apps.core.plugins import get_plugin_context

            context.update(get_plugin_context(campaign, subscriber))

            subject, html, text = render_saved_template(
                template,
                context,
                subject_override=campaign.subject_line,
            )

            headers = build_unsubscribe_headers(subscriber, email_type)

            provider_id = backend.send(
                to=subscriber.email,
                subject=subject,
                html=html,
                text=text,
                headers=headers,
            )

            email_send = create_email_send(
                subscriber=subscriber,
                campaign=campaign,
                email_type=email_type,
                subject_line=subject,
            )
            mark_sent(email_send, provider_message_id=provider_id or "")
            result.sent += 1

        except Exception as exc:
            logger.exception(
                "Failed to send campaign %s to %s", campaign_id, subscriber.email,
            )
            result.failed += 1
            error_msg = f"{subscriber.email}: {exc}"
            result.errors.append(error_msg)

            try:
                email_send = create_email_send(
                    subscriber=subscriber,
                    campaign=campaign,
                    email_type=email_type,
                    subject_line=campaign.subject_line,
                )
                mark_failed(email_send, error_message=str(exc))
            except Exception:
                logger.exception(
                    "Failed to create EmailSend failure record for %s", subscriber.email,
                )

    now = timezone.now()
    campaign.status = Campaign.Status.SENT
    campaign.sent_at = now
    campaign.recipient_count = result.sent
    campaign.save(update_fields=["status", "sent_at", "recipient_count", "updated_at"])

    return result


# ---------------------------------------------------------------------------
# Test email
# ---------------------------------------------------------------------------


class TestEmailError(Exception):
    """Raised when a test email cannot be sent."""

    __test__ = False  # Prevent pytest from trying to collect this as a test class.


@dataclass
class TestEmailResult:
    """Result of a single test email send."""

    recipient: str
    subject: str
    provider_message_id: str | None = None


def send_test_email(
    campaign_id: int,
    recipient_email: str,
) -> TestEmailResult:
    """Send a single test email for a campaign without creating production logs.

    Renders the campaign template with a synthetic context (no real subscriber
    required), includes RFC 8058 unsubscribe headers and a visible unsubscribe
    link, and dispatches via the configured email backend. No EmailSend record
    is created — this is purely a preview/test mechanism.

    Parameters
    ----------
    campaign_id:
        Primary key of the campaign to test.
    recipient_email:
        The email address to send the test to.

    Returns
    -------
    TestEmailResult
        Summary including the recipient, rendered subject, and provider ID.

    Raises
    ------
    TestEmailError
        If the campaign does not exist.
    """
    try:
        campaign = Campaign.objects.select_related("email_type", "template").get(
            pk=campaign_id,
        )
    except Campaign.DoesNotExist:
        raise TestEmailError(f"Campaign {campaign_id} not found.") from None

    template = campaign.template
    backend = get_backend()

    base_url = _get_base_url()
    visible_unsubscribe_url = f"{base_url}/unsubscribe/?token=test-recipient"
    one_click_url = f"{base_url}/unsubscribe/one-click/?token=test-recipient"

    context = {
        "subscriber_name": recipient_email,
        "subscriber_email": recipient_email,
        "unsubscribe_url": visible_unsubscribe_url,
    }

    from apps.core.plugins import get_plugin_context

    context.update(get_plugin_context(campaign, None))

    subject, html, text = render_saved_template(
        template,
        context,
        subject_override=campaign.subject_line,
    )

    headers = {
        "List-Unsubscribe": f"<{one_click_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }

    provider_id = backend.send(
        to=recipient_email,
        subject=subject,
        html=html,
        text=text,
        headers=headers,
    )

    return TestEmailResult(
        recipient=recipient_email,
        subject=subject,
        provider_message_id=provider_id,
    )


def _get_base_url() -> str:
    from django.conf import settings

    return settings.POSTINO_BASE_URL.rstrip("/")


# ---------------------------------------------------------------------------
# Sequence services
# ---------------------------------------------------------------------------


class EnrollmentError(Exception):
    """Raised when enrollment cannot proceed."""


def enroll_subscriber(
    subscriber: Subscriber,
    sequence: Sequence,
) -> SequenceEnrollment:
    """Enroll a subscriber in a sequence.

    Checks that the subscriber is active, the sequence is active, and the
    subscriber is not already enrolled. Sets current_step to the first step.
    """
    if not sequence.is_active:
        raise EnrollmentError(f"Cannot enroll into inactive sequence '{sequence.name}'.")

    if subscriber.is_suppressed:
        raise EnrollmentError(
            f"Cannot enroll suppressed subscriber {subscriber.email}."
        )

    if subscriber.status != Subscriber.Status.ACTIVE:
        raise EnrollmentError(
            f"Cannot enroll non-active subscriber {subscriber.email}."
        )

    enrollment, created = SequenceEnrollment.objects.get_or_create(
        subscriber=subscriber,
        sequence=sequence,
        defaults={
            "current_step": sequence.steps.first(),
            "status": SequenceEnrollment.Status.ACTIVE,
        },
    )

    if not created and enrollment.status in (
        SequenceEnrollment.Status.CANCELLED,
        SequenceEnrollment.Status.PAUSED,
    ):
        raise EnrollmentError(
            f"Subscriber {subscriber.email} was previously "
            f"{enrollment.get_status_display()} in '{sequence.name}'."
        )

    return enrollment


def _evaluate_step_condition(
    step: SequenceStep,
    subscriber: Subscriber,
) -> bool:
    """Return True if the subscriber passes the step's condition filter.

    Empty/None condition → always pass. Supported keys:
    - ``health_below``: health_score < value.
    - ``health_above``: health_score >= value.
    - ``has_tag``: subscriber has the named tag.
    """
    condition = step.condition
    if not condition:
        return True

    if "health_below" in condition:
        if subscriber.health_score >= condition["health_below"]:
            return False

    if "health_above" in condition:
        if subscriber.health_score < condition["health_above"]:
            return False

    if "has_tag" in condition:
        if not subscriber.tags.filter(name=condition["has_tag"]).exists():
            return False

    return True


@dataclass
class SequenceEvalResult:
    """Summary of a sequence evaluation run."""

    enrollments_processed: int = 0
    emails_sent: int = 0
    emails_skipped: int = 0
    enrollments_completed: int = 0
    enrollments_cancelled: int = 0
    errors: list[str] = field(default_factory=list)


def evaluate_sequences() -> SequenceEvalResult:
    """Process all active enrollments and send due sequence emails.

    For each active enrollment:
    1. Check subscriber is still active and not suppressed.
    2. If suppressed, auto-cancel the enrollment.
    3. Check if enough time has passed since enrollment for the current step.
    4. Evaluate step condition filter.
    5. Verify consent via ``can_send_to_subscriber``.
    6. Render template, add unsubscribe headers, send email.
    7. Create EmailSend record linked to the sequence_step.
    8. Advance current_step or mark enrollment as completed.
    """
    result = SequenceEvalResult()
    now = timezone.now()
    backend = get_backend()

    active_enrollments = SequenceEnrollment.objects.filter(
        status=SequenceEnrollment.Status.ACTIVE,
        sequence__is_active=True,
    ).select_related(
        "subscriber",
        "sequence",
        "current_step",
        "current_step__email_type",
        "current_step__template",
    )

    for enrollment in active_enrollments:
        result.enrollments_processed += 1
        subscriber = enrollment.subscriber
        step = enrollment.current_step

        if step is None:
            enrollment.status = SequenceEnrollment.Status.COMPLETED
            enrollment.completed_at = now
            enrollment.save(update_fields=["status", "completed_at", "updated_at"])
            result.enrollments_completed += 1
            continue

        if subscriber.is_suppressed:
            _cancel_enrollment(enrollment)
            result.enrollments_cancelled += 1
            continue

        if not can_send_to_subscriber(subscriber, step.email_type):
            _cancel_enrollment(enrollment)
            result.enrollments_cancelled += 1
            continue

        elapsed = now - enrollment.enrolled_at
        if elapsed < timedelta(hours=step.delay_hours):
            continue

        if not _evaluate_step_condition(step, subscriber):
            result.emails_skipped += 1
            continue

        try:
            subject_override = step.subject_override or None
            unsubscribe_url = build_unsubscribe_url(subscriber, step.email_type)
            context = {
                "subscriber_name": subscriber.name,
                "subscriber_email": subscriber.email,
                "unsubscribe_url": unsubscribe_url,
            }

            from apps.core.plugins import get_plugin_context

            context.update(get_plugin_context(None, subscriber))

            subject, html, text = render_saved_template(
                step.template,
                context,
                subject_override=subject_override,
            )

            headers = build_unsubscribe_headers(subscriber, step.email_type)

            provider_id = backend.send(
                to=subscriber.email,
                subject=subject,
                html=html,
                text=text,
                headers=headers,
            )

            email_send = create_email_send(
                subscriber=subscriber,
                sequence_step=step,
                email_type=step.email_type,
                subject_line=subject,
            )
            mark_sent(email_send, provider_message_id=provider_id or "")
            result.emails_sent += 1

            _advance_enrollment(enrollment)

        except Exception as exc:
            logger.exception(
                "Failed to send sequence step %s to %s",
                step.pk, subscriber.email,
            )
            result.errors.append(f"{subscriber.email} step {step.order}: {exc}")

    return result


def _cancel_enrollment(enrollment: SequenceEnrollment) -> None:
    """Cancel an active enrollment (e.g. subscriber suppressed)."""
    enrollment.status = SequenceEnrollment.Status.CANCELLED
    enrollment.save(update_fields=["status", "updated_at"])


def _advance_enrollment(enrollment: SequenceEnrollment) -> None:
    """Move enrollment to the next step or mark completed."""
    current = enrollment.current_step
    next_step = (
        SequenceStep.objects.filter(
            sequence=enrollment.sequence,
            order__gt=current.order,
        )
        .order_by("order")
        .first()
    )

    if next_step is None:
        enrollment.status = SequenceEnrollment.Status.COMPLETED
        enrollment.completed_at = timezone.now()
        enrollment.current_step = None
        enrollment.save(
            update_fields=["status", "completed_at", "current_step", "updated_at"]
        )
    else:
        enrollment.current_step = next_step
        enrollment.save(update_fields=["current_step", "updated_at"])


def pause_sequence(sequence: Sequence) -> int:
    """Pause a sequence by setting is_active=False and pausing active enrollments.

    Returns the number of enrollments paused.
    """
    sequence.is_active = False
    sequence.save(update_fields=["is_active", "updated_at"])

    count = SequenceEnrollment.objects.filter(
        sequence=sequence,
        status=SequenceEnrollment.Status.ACTIVE,
    ).update(status=SequenceEnrollment.Status.PAUSED, updated_at=timezone.now())

    return count


def resume_sequence(sequence: Sequence) -> int:
    """Resume a sequence by setting is_active=True and reactivating paused enrollments.

    Returns the number of enrollments reactivated.
    """
    sequence.is_active = True
    sequence.save(update_fields=["is_active", "updated_at"])

    enrollments = SequenceEnrollment.objects.filter(
        sequence=sequence,
        status=SequenceEnrollment.Status.PAUSED,
    ).select_related("subscriber")

    count = 0
    for enrollment in enrollments:
        if enrollment.subscriber.is_suppressed:
            _cancel_enrollment(enrollment)
        else:
            enrollment.status = SequenceEnrollment.Status.ACTIVE
            enrollment.save(update_fields=["status", "updated_at"])
            count += 1

    return count


def cancel_enrollments_for_subscriber(subscriber: Subscriber) -> int:
    """Cancel all active enrollments for a suppressed/unsubscribed subscriber.

    Called from the unsubscribe flow and webhook handlers.
    """
    count = SequenceEnrollment.objects.filter(
        subscriber=subscriber,
        status__in=[SequenceEnrollment.Status.ACTIVE, SequenceEnrollment.Status.PAUSED],
    ).update(status=SequenceEnrollment.Status.CANCELLED, updated_at=timezone.now())

    return count


# ---------------------------------------------------------------------------
# Sequence triggers
# ---------------------------------------------------------------------------


def trigger_sequences_for_subscriber_created(subscriber: Subscriber) -> list[Sequence]:
    """Enroll subscriber in matching ``subscriber_created`` sequences.

    Evaluates trigger_config for each active sequence with that trigger type.
    Supported trigger_config keys:
    - ``{}`` / absent → match all.
    - ``{"source": "signup_form"}`` → match only if subscriber.source matches.
    - ``{"tags": ["pro"]}`` → match only if subscriber has at least one tag.

    Returns list of sequences that matched (regardless of enrollment outcome).
    """
    sequences = Sequence.objects.filter(
        trigger_type=Sequence.TriggerType.SUBSCRIBER_CREATED,
        is_active=True,
    )

    matched: list[Sequence] = []
    for seq in sequences:
        if _trigger_matches(seq.trigger_config, subscriber):
            matched.append(seq)
            try:
                enroll_subscriber(subscriber, seq)
            except EnrollmentError:
                pass

    return matched


def trigger_sequences_for_tag_added(
    subscriber: Subscriber, tag_name: str,
) -> list[Sequence]:
    """Enroll subscriber in matching ``tag_added`` sequences.

    Empty trigger_config matches all tag additions. Supported explicit forms:
    ``{"tag": "pro"}``, ``{"tags": "pro"}``, ``{"tags": ["pro"]}``.
    """
    sequences = Sequence.objects.filter(
        trigger_type=Sequence.TriggerType.TAG_ADDED,
        is_active=True,
    )

    matched: list[Sequence] = []
    for seq in sequences:
        config = seq.trigger_config or {}
        required_tags = _coerce_string_list(config.get("tags") or config.get("tag"))
        tag_matches = not required_tags or tag_name in required_tags
        source_config = {key: value for key, value in config.items() if key not in {"tag", "tags"}}
        if tag_matches and _trigger_matches(source_config, subscriber):
            matched.append(seq)
            try:
                enroll_subscriber(subscriber, seq)
            except EnrollmentError:
                pass

    return matched


def _trigger_matches(trigger_config: dict, subscriber: Subscriber) -> bool:
    """Evaluate whether a trigger_config matches the given subscriber."""
    config = trigger_config or {}
    if not config:
        return True

    required_source = config.get("source")
    if required_source and subscriber.source != required_source:
        return False

    required_tags = config.get("tags")
    if required_tags:
        tag_names = _coerce_string_list(required_tags)
        if not subscriber.tags.filter(name__in=tag_names).exists():
            return False

    return True


def _coerce_string_list(value: object) -> list[str]:
    """Normalize a trigger config string/list value to a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)]
