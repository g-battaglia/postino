"""Subscriber service functions for Postino.

Business logic for creating, listing, exporting, tagging, suppressing
subscribers, and computing health scores. All multi-step operations use
``transaction.atomic()`` to guarantee consistency.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.consent.models import ConsentRecord, UnsubscribeEvent
from apps.consent.services import process_global_unsubscribe
from apps.consent.tokens import generate_double_optin_token
from apps.subscribers.models import DataSource, Subscriber, SyncLog, Tag

_SUPPRESSED_STATUSES = frozenset({"unsubscribed", "bounced", "complained", "deleted"})


class SuppressedSubscriberError(Exception):
    """Raised when trying to add a subscriber whose email is suppressed."""


def add_subscriber(
    email: str,
    *,
    name: str = "",
    source: str = "manual",
    source_id: str = "",
    metadata: dict | None = None,
    tag_names: list[str] | None = None,
    ip_address: str | None = None,
) -> Subscriber:
    """Create a new subscriber or return an existing non-suppressed one.

    - If the email belongs to a suppressed subscriber, raises
      ``SuppressedSubscriberError``.
    - If the email appears in any ``UnsubscribeEvent`` (even without a
      current ``Subscriber`` row), raises ``SuppressedSubscriberError``.
    - If the email already exists and is not suppressed, returns the
      existing subscriber unchanged.
    - When ``POSTINO_REQUIRE_DOUBLE_OPTIN`` is True, the new subscriber
      starts in PENDING status with a double-optin token.  No consent
      grant is created here; ``confirm_double_optin()`` creates it.
    - When double opt-in is disabled, the subscriber is ACTIVE immediately
      and a ``ConsentRecord`` with action ``"grant"`` is created.
    - Tags are resolved by name and attached to the new subscriber.
    """
    normalized_email = email.strip().lower()

    existing = Subscriber.objects.filter(email=normalized_email).first()
    if existing is not None:
        if existing.is_suppressed:
            raise SuppressedSubscriberError(
                f"Cannot add suppressed subscriber: {normalized_email}"
            )
        return existing

    if UnsubscribeEvent.objects.filter(email=normalized_email).exists():
        raise SuppressedSubscriberError(
            f"Email is historically suppressed: {normalized_email}"
        )

    with transaction.atomic():
        require_double_optin = settings.POSTINO_REQUIRE_DOUBLE_OPTIN
        status = Subscriber.Status.PENDING if require_double_optin else Subscriber.Status.ACTIVE

        subscriber = Subscriber(
            email=normalized_email,
            name=name,
            status=status,
            source=source,
            source_id=source_id,
            metadata=metadata or {},
            ip_address=ip_address,
        )

        if require_double_optin:
            subscriber.double_optin_token = generate_double_optin_token(subscriber.id)

        subscriber.save()

        if not require_double_optin:
            ConsentRecord.objects.create(
                subscriber=subscriber,
                email_type=None,
                action=ConsentRecord.Action.GRANT,
                method=source,
                ip_address=ip_address,
            )

        if tag_names:
            tags = Tag.objects.filter(name__in=tag_names)
            subscriber.tags.set(tags)

        evaluate_auto_tag(subscriber)

    if subscriber.status == Subscriber.Status.ACTIVE:
        from apps.campaigns.services import trigger_sequences_for_subscriber_created

        trigger_sequences_for_subscriber_created(subscriber)

    return subscriber


def get_subscriber(identifier: str) -> Subscriber:
    """Look up a subscriber by email or UUID string.

    Raises ``Subscriber.DoesNotExist`` if not found.
    """
    try:
        uuid.UUID(identifier)
        return Subscriber.objects.get(id=identifier)
    except ValueError:
        return Subscriber.objects.get(email=identifier.strip().lower())


def list_subscribers(
    *,
    status: str | None = None,
    tag: str | None = None,
    health_below: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Subscriber], int]:
    """Return ``(subscribers, total_count)`` with optional filters."""
    qs = Subscriber.objects.all()

    if status is not None:
        qs = qs.filter(status=status)
    if tag is not None:
        qs = qs.filter(tags__name=tag)
    if health_below is not None:
        qs = qs.filter(health_score__lt=health_below)

    total = qs.count()
    subscribers = list(qs[offset : offset + limit])
    return subscribers, total


def count_subscribers(*, status: str | None = None, tag: str | None = None) -> int:
    """Return subscriber count with optional filters."""
    qs = Subscriber.objects.all()
    if status is not None:
        qs = qs.filter(status=status)
    if tag is not None:
        qs = qs.filter(tags__name=tag)
    return qs.count()


def export_subscriber_data(subscriber: Subscriber) -> dict:
    """Collect all GDPR-relevant data for a subscriber.

    Returns a dict with the subscriber's fields, consent records,
    unsubscribe events, and tag names. Used by the CLI ``gdpr export``
    command and the future web data-export endpoint.
    """
    consent_records = list(
        ConsentRecord.objects.filter(subscriber=subscriber).values(
            "id", "email_type_id", "action", "method", "ip_address", "proof", "created_at",
        )
    )
    unsubscribe_events = list(
        UnsubscribeEvent.objects.filter(subscriber=subscriber).values(
            "id", "email", "email_type_id", "method", "ip_address", "user_agent", "created_at",
        )
    )
    tag_names = list(subscriber.tags.values_list("name", flat=True))

    return {
        "subscriber": {
            "id": str(subscriber.id),
            "email": subscriber.email,
            "name": subscriber.name,
            "status": subscriber.status,
            "source": subscriber.source,
            "source_id": subscriber.source_id,
            "metadata": subscriber.metadata,
            "health_score": subscriber.health_score,
            "last_activity_at": (
                subscriber.last_activity_at.isoformat()
                if subscriber.last_activity_at
                else None
            ),
            "ip_address": str(subscriber.ip_address) if subscriber.ip_address else None,
            "double_optin_confirmed_at": (
                subscriber.double_optin_confirmed_at.isoformat()
                if subscriber.double_optin_confirmed_at
                else None
            ),
            "created_at": subscriber.created_at.isoformat(),
            "updated_at": subscriber.updated_at.isoformat(),
        },
        "consent_records": consent_records,
        "unsubscribe_events": unsubscribe_events,
        "tags": tag_names,
    }


def tag_subscriber(subscriber: Subscriber, tag_name: str, *, remove: bool = False) -> None:
    """Add or remove a tag from a subscriber.

    Creates the tag if it doesn't exist when adding.
    Triggers ``tag_added`` sequences when adding a tag to an active subscriber.
    """
    if remove:
        tag = Tag.objects.filter(name=tag_name).first()
        if tag is not None:
            subscriber.tags.remove(tag)
        return

    tag, _ = Tag.objects.get_or_create(
        name=tag_name,
        defaults={"display_name": tag_name.replace("-", " ").replace("_", " ").title()},
    )
    subscriber.tags.add(tag)

    if not subscriber.is_suppressed:
        from apps.campaigns.services import trigger_sequences_for_tag_added

        trigger_sequences_for_tag_added(subscriber, tag_name)


def suppress_subscriber(subscriber: Subscriber, *, reason: str = "manual") -> UnsubscribeEvent:
    """Suppress a subscriber (global unsubscribe via CLI or admin).

    Delegates to ``consent.services.process_global_unsubscribe()``.
    """
    return process_global_unsubscribe(subscriber, method=reason)


# ---------------------------------------------------------------------------
# Health score computation
# ---------------------------------------------------------------------------


@dataclass
class HealthScoreResult:
    """Summary of a health score computation run."""

    total: int = 0
    updated: int = 0
    distribution: dict[str, int] = field(
        default_factory=lambda: {"healthy": 0, "at_risk": 0, "critical": 0}
    )


def _score_last_activity(subscriber: Subscriber, now: timezone.datetime) -> float:
    """Score the last activity factor (weight 40%).

    Returns 0-100 based on time since last_activity_at.
    """
    if subscriber.last_activity_at is None:
        return 0.0
    days_since = (now - subscriber.last_activity_at).days
    if days_since < 7:
        return 100.0
    if days_since < 14:
        return 70.0
    if days_since < 30:
        return 40.0
    if days_since < 90:
        return 10.0
    return 0.0


def _score_email_engagement(subscriber: Subscriber) -> float:
    """Score email engagement factor (weight 30%).

    Based on open/click rate of the last 10 emails sent to this subscriber.
    """
    from apps.campaigns.models import EmailSend

    recent_sends = list(
        EmailSend.objects.filter(
            subscriber=subscriber,
            status__in=["sent", "delivered", "opened", "clicked"],
        )
        .exclude(sent_at__isnull=True)
        .order_by("-sent_at")
        .values("status")[:10]
    )

    if not recent_sends:
        return 50.0

    opened_or_clicked = sum(
        1 for s in recent_sends if s["status"] in ("opened", "clicked")
    )
    engagement_rate = opened_or_clicked / len(recent_sends)
    return min(100.0, engagement_rate * 100.0)


def _score_subscription_tenure(subscriber: Subscriber, now: timezone.datetime) -> float:
    """Score subscription tenure factor (weight 15%).

    Returns 0-100 based on how long the subscriber has been active.
    """
    days_since_created = (now - subscriber.created_at).days
    if days_since_created > 180:
        return 100.0
    if days_since_created > 90:
        return 70.0
    if days_since_created > 30:
        return 40.0
    return 20.0


def _score_source_quality(subscriber: Subscriber) -> float:
    """Score source quality factor (weight 15%).

    Double opt-in is highest quality, signup form next, then sync/import.
    """
    if subscriber.double_optin_confirmed_at is not None:
        return 100.0
    source_scores: dict[str, float] = {
        "signup_form": 80.0,
        "manual": 80.0,
        "sync": 50.0,
        "import": 50.0,
    }
    return source_scores.get(subscriber.source, 50.0)


def compute_subscriber_health_score(subscriber: Subscriber) -> int:
    """Compute a deterministic health score (0-100) for a single subscriber.

    Weighted factors per PLAN.md section 9:
    - Last activity: 40%
    - Email engagement: 30%
    - Subscription tenure: 15%
    - Source quality: 15%

    Labels: 70-100 Healthy, 40-69 At-risk, 0-39 Critical.
    """
    now = timezone.now()

    activity = _score_last_activity(subscriber, now)
    engagement = _score_email_engagement(subscriber)
    tenure = _score_subscription_tenure(subscriber, now)
    source = _score_source_quality(subscriber)

    raw = (
        activity * 0.40
        + engagement * 0.30
        + tenure * 0.15
        + source * 0.15
    )

    return max(0, min(100, round(raw)))


def compute_all_health_scores() -> HealthScoreResult:
    """Recompute health scores for all active subscribers.

    Returns a HealthScoreResult with counts and distribution.
    Designed to be called from the ``compute_health_scores`` management command.
    """
    active_subs = Subscriber.objects.filter(status=Subscriber.Status.ACTIVE)
    total = active_subs.count()
    updated = 0

    distribution = {"healthy": 0, "at_risk": 0, "critical": 0}

    for subscriber in active_subs.iterator(chunk_size=500):
        new_score = compute_subscriber_health_score(subscriber)
        if subscriber.health_score != new_score:
            subscriber.health_score = new_score
            subscriber.save(update_fields=["health_score", "updated_at"])
            updated += 1

        if new_score >= 70:
            distribution["healthy"] += 1
        elif new_score >= 40:
            distribution["at_risk"] += 1
        else:
            distribution["critical"] += 1

    return HealthScoreResult(total=total, updated=updated, distribution=distribution)


# ---------------------------------------------------------------------------
# Auto-tagging
# ---------------------------------------------------------------------------


def evaluate_auto_tag(subscriber: Subscriber) -> list[Tag]:
    """Evaluate all auto-tag rules against a subscriber and apply matching tags.

    An auto_rule is a JSON dict on Tag where each key is a dotted path into
    subscriber metadata/status/source/health_score and the value is the
    expected match. Example: ``{"metadata.plan": "pro", "status": "active"}``.

    Returns the list of newly applied tags.
    """
    applied: list[Tag] = []
    auto_tags = Tag.objects.exclude(auto_rule__isnull=True).exclude(auto_rule="")

    for tag in auto_tags:
        if not isinstance(tag.auto_rule, dict):
            continue
        if _rule_matches(tag.auto_rule, subscriber):
            if not subscriber.tags.filter(pk=tag.pk).exists():
                subscriber.tags.add(tag)
                applied.append(tag)

    return applied


def _get_nested(obj: dict, dotted_key: str, default: object = None) -> object:
    """Resolve a dotted key like ``metadata.plan`` from a dict."""
    keys = dotted_key.split(".")
    current: object = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
    return current if current is not None else default


def _rule_matches(rule: dict, subscriber: Subscriber) -> bool:
    """Check whether all rule conditions match the given subscriber."""
    context = {
        "status": subscriber.status,
        "source": subscriber.source,
        "health_score": subscriber.health_score,
        "metadata": subscriber.metadata or {},
    }

    for key, expected in rule.items():
        value = _get_nested(context, key)
        if str(value) != str(expected):
            return False

    return True


# ---------------------------------------------------------------------------
# Sync service
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Summary of a single data source sync run."""

    source_name: str
    new_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    suppressed_count: int = 0
    errors: list[str] = field(default_factory=list)


def sync_data_source(
    data_source: DataSource,
    *,
    dry_run: bool = False,
) -> SyncResult:
    """Sync subscribers from a DataSource.

    Reads rows from the configured database, maps fields, checks suppression
    history, creates or updates subscribers, applies default tag and auto-tags.

    Never re-subscribes suppressed users.
    """
    result = SyncResult(source_name=data_source.name)

    config = data_source.config
    database_url = config.get("database_url", "")
    query = config.get("query", "")
    field_map = config.get("field_map", {})
    tag_name = str(config.get("tag", "")).strip()
    tag_names = _sync_tag_names(data_source, tag_name)
    metadata_fields = config.get("metadata_fields", [])

    if not database_url or not query:
        result.errors.append("Missing database_url or query in source config.")
        return result

    email_col = field_map.get("email", "email")
    name_col = field_map.get("name", "")
    source_id_col = field_map.get("source_id", "")

    try:
        rows = _execute_sync_query(database_url, query)
    except Exception as exc:
        result.errors.append(f"Database query failed: {exc}")
        return result

    for row in rows:
        email = str(row.get(email_col, "")).strip().lower()
        if not email:
            result.skipped_count += 1
            continue

        name = str(row.get(name_col, "")).strip() if name_col else ""
        source_id = str(row.get(source_id_col, "")).strip() if source_id_col else ""

        meta = {}
        for mf in metadata_fields:
            if mf in row:
                meta[mf] = row[mf]

        suppressed = _is_email_suppressed(email)
        if suppressed:
            result.suppressed_count += 1
            continue

        if dry_run:
            existing = Subscriber.objects.filter(email=email).first()
            if existing:
                result.updated_count += 1
            else:
                result.new_count += 1
            continue

        with transaction.atomic():
            existing = Subscriber.objects.filter(email=email).first()

            if existing is not None:
                changed = False
                if name and existing.name != name:
                    existing.name = name
                    changed = True
                if source_id and existing.source_id != source_id:
                    existing.source_id = source_id
                    changed = True
                existing_metadata = existing.metadata or {}
                if meta and existing_metadata != {**existing_metadata, **meta}:
                    existing.metadata = {**existing_metadata, **meta}
                    changed = True
                if changed:
                    existing.source = Subscriber.Source.SYNC
                    existing.save(
                        update_fields=["name", "source_id", "metadata", "source", "updated_at"]
                    )
                if tag_names:
                    _apply_sync_tags(existing, tag_names)
                evaluate_auto_tag(existing)
                result.updated_count += 1
            else:
                subscriber = Subscriber(
                    email=email,
                    name=name,
                    source=Subscriber.Source.SYNC,
                    source_id=source_id,
                    metadata=meta,
                    status=Subscriber.Status.ACTIVE,
                )
                subscriber.save()

                ConsentRecord.objects.create(
                    subscriber=subscriber,
                    email_type=None,
                    action=ConsentRecord.Action.GRANT,
                    method="sync",
                )

                if tag_names:
                    _apply_sync_tags(subscriber, tag_names)

                evaluate_auto_tag(subscriber)
                result.new_count += 1

    return result


def _sync_tag_names(data_source: DataSource, config_tag_name: str) -> list[str]:
    """Return unique tag names configured for a data source sync."""
    tag_names: list[str] = []
    if config_tag_name:
        tag_names.append(config_tag_name)
    if data_source.default_tag_id and data_source.default_tag.name not in tag_names:
        tag_names.append(data_source.default_tag.name)
    return tag_names


def _apply_sync_tags(subscriber: Subscriber, tag_names: list[str]) -> None:
    """Attach sync tags without bypassing missing-tag creation."""
    for tag_name in tag_names:
        tag_obj, _ = Tag.objects.get_or_create(
            name=tag_name,
            defaults={
                "display_name": tag_name.replace("-", " ").replace("_", " ").title()
            },
        )
        subscriber.tags.add(tag_obj)


def _is_email_suppressed(email: str) -> bool:
    """Check whether an email is suppressed by subscriber status or unsubscribe history."""
    existing = Subscriber.objects.filter(email=email).first()
    if existing is not None and existing.is_suppressed:
        return True
    if UnsubscribeEvent.objects.filter(email=email).exists():
        return True
    return False


def _execute_sync_query(database_url: str, query: str) -> list[dict]:
    """Execute a SQL query against an external database and return rows as dicts.

    For SQLite databases, uses a direct sqlite3 connection.
    For PostgreSQL, uses psycopg directly.
    """
    if "sqlite" in database_url:
        import sqlite3 as _sqlite3

        db_path = database_url.replace("sqlite:///", "").replace("sqlite://", "")
        conn = _sqlite3.connect(db_path)
        try:
            conn.row_factory = _sqlite3.Row
            cursor = conn.execute(query)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row, strict=True)) for row in rows]
        finally:
            conn.close()

    import psycopg

    conn = psycopg.connect(database_url)
    try:
        with conn.cursor() as cursor:
            cursor.execute(query)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row, strict=True)) for row in rows]
    finally:
        conn.close()


def get_sync_sources() -> list[dict]:
    """Return configured data sources from TOML config as dicts."""
    from cli.config import load_config

    config = load_config()
    return [s.model_dump() for s in config.sources]


def get_sync_log_summary() -> list[dict]:
    """Return recent sync log entries for all sources."""
    logs = SyncLog.objects.select_related("data_source").order_by("-started_at")[:20]
    return [
        {
            "source": log.data_source.name if log.data_source_id else "unknown",
            "status": log.status,
            "new_count": log.new_count,
            "updated_count": log.updated_count,
            "skipped_count": log.skipped_count,
            "suppressed_count": log.suppressed_count,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            "error_details": log.error_details,
        }
        for log in logs
    ]
