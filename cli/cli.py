"""Click CLI entry point for Postino."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

import click

from cli.config import ConfigError, PostinoConfig, load_config, redact_config


def _json_envelope(ok: bool, data: Any = None, error: str | None = None) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "ok": ok,
        "meta": {"timestamp": datetime.now(UTC).isoformat()},
    }
    if data is not None:
        envelope["data"] = data
    if error is not None:
        envelope["error"] = error
    return envelope


def _version_string() -> str:
    try:
        from importlib import metadata

        return metadata.version("postino")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def _try_load_config() -> PostinoConfig:
    try:
        return load_config()
    except ConfigError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


_django_ready = False


def _setup_django() -> None:
    """Initialize Django ORM — idempotent, safe to call multiple times."""
    global _django_ready
    if _django_ready:
        return
    import django

    django.setup()
    _django_ready = True


def _subscriber_to_dict(subscriber: Any) -> dict[str, Any]:
    """Serialize a Subscriber model instance to a plain dict for JSON output."""
    return {
        "id": str(subscriber.id),
        "email": subscriber.email,
        "name": subscriber.name,
        "status": subscriber.status,
        "health_score": subscriber.health_score,
        "source": subscriber.source,
        "source_id": subscriber.source_id,
        "metadata": subscriber.metadata,
        "tags": list(subscriber.tags.values_list("name", flat=True)),
        "created_at": subscriber.created_at.isoformat(),
        "updated_at": subscriber.updated_at.isoformat(),
    }


def _sequence_to_dict(sequence: Any) -> dict[str, Any]:
    """Serialize a Sequence model instance to a plain dict for JSON output."""
    return {
        "id": sequence.pk,
        "name": sequence.name,
        "slug": sequence.slug,
        "description": sequence.description,
        "is_active": sequence.is_active,
        "trigger_type": sequence.trigger_type,
        "trigger_config": sequence.trigger_config,
        "steps": list(sequence.steps.values_list("order", flat=True).order_by("order")),
        "created_at": sequence.created_at.isoformat(),
        "updated_at": sequence.updated_at.isoformat(),
    }


def _campaign_to_dict(campaign: Any) -> dict[str, Any]:
    """Serialize a Campaign model instance to a plain dict for JSON output."""
    data: dict[str, Any] = {
        "id": campaign.pk,
        "name": campaign.name,
        "status": campaign.status,
        "email_type": campaign.email_type.slug if campaign.email_type_id else None,
        "template": campaign.template.slug if campaign.template_id else None,
        "subject_line": campaign.subject_line,
        "audience_filter": campaign.audience_filter,
        "recipient_count": campaign.recipient_count,
        "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
        "sent_at": campaign.sent_at.isoformat() if campaign.sent_at else None,
        "created_at": campaign.created_at.isoformat(),
        "updated_at": campaign.updated_at.isoformat(),
    }
    return data


def _template_to_dict(template: Any) -> dict[str, Any]:
    """Serialize an EmailTemplate model instance to a plain dict for JSON output."""
    return {
        "id": template.pk,
        "name": template.name,
        "slug": template.slug,
        "subject_default": template.subject_default,
        "html_body": template.html_body,
        "text_body": template.text_body,
        "created_at": template.created_at.isoformat(),
        "updated_at": template.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
def main() -> None:
    """Postino - GDPR-first email marketing for Django."""


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def version(as_json: bool) -> None:
    """Print the Postino version."""
    v = _version_string()
    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data={"version": v}), indent=2))
    else:
        click.echo(f"postino {v}")


# ---------------------------------------------------------------------------
# config group
# ---------------------------------------------------------------------------


@main.group()
def config() -> None:
    """Configuration commands."""


@config.command(name="validate")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def config_validate(as_json: bool) -> None:
    """Validate the config file and exit."""
    try:
        load_config()
        if as_json:
            click.echo(json.dumps(_json_envelope(ok=True, data={"valid": True}), indent=2))
        else:
            click.echo("Configuration is valid.")
    except ConfigError as exc:
        if as_json:
            click.echo(
                json.dumps(_json_envelope(ok=False, error=str(exc)), indent=2), err=True
            )
        else:
            click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)


@config.command(name="show")
@click.option("--section", default=None, help="Show only a specific config section.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def config_show(section: str | None, as_json: bool) -> None:
    """Display the current configuration (secrets are redacted)."""
    cfg = _try_load_config()
    data = redact_config(cfg.model_dump(mode="json"))

    if section is not None:
        if section not in data:
            if as_json:
                click.echo(
                    json.dumps(
                        _json_envelope(ok=False, error=f"Unknown section: {section}"),
                        indent=2,
                    ),
                    err=True,
                )
            else:
                click.echo(f"Unknown section: {section}", err=True)
            sys.exit(1)
        data = {section: data[section]}

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        for section_name, values in data.items():
            click.echo(f"[{section_name}]")
            if isinstance(values, dict):
                for key, val in values.items():
                    click.echo(f"  {key} = {_format_value(val)}")
            elif isinstance(values, list):
                for idx, item in enumerate(values):
                    click.echo(f"  [[{section_name}]]  # entry {idx}")
                    if isinstance(item, dict):
                        for key, val in item.items():
                            click.echo(f"    {key} = {_format_value(val)}")
            click.echo()


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"' if value else '""'
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)


# ---------------------------------------------------------------------------
# subscribers group
# ---------------------------------------------------------------------------


@main.group()
def subscribers() -> None:
    """Subscriber management commands."""


@subscribers.command(name="list")
@click.option("--status", default=None, help="Filter by status.")
@click.option("--tag", default=None, help="Filter by tag name.")
@click.option("--health-below", type=int, default=None, help="Filter by health score below N.")
@click.option("--limit", type=int, default=50, help="Max results.")
@click.option("--offset", type=int, default=0, help="Offset for pagination.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def subscribers_list(
    status: str | None,
    tag: str | None,
    health_below: int | None,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    """List subscribers with optional filters."""
    _setup_django()
    from apps.subscribers.services import list_subscribers

    subs, total = list_subscribers(
        status=status, tag=tag, health_below=health_below, limit=limit, offset=offset,
    )

    if as_json:
        data = {
            "subscribers": [_subscriber_to_dict(s) for s in subs],
            "total": total,
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        if not subs:
            click.echo("No subscribers found.")
            return
        click.echo(f"{'ID':<38} {'Email':<32} {'Name':<20} {'Status':<14} {'Health'}")
        click.echo("-" * 116)
        for s in subs:
            click.echo(
                f"{str(s.id):<38} {s.email:<32} {s.name:<20} {s.status:<14} {s.health_score}"
            )
        click.echo(f"\nShowing {len(subs)} of {total}")


@subscribers.command(name="get")
@click.argument("identifier")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def subscribers_get(identifier: str, as_json: bool) -> None:
    """Get details for a single subscriber."""
    _setup_django()
    from apps.subscribers.models import Subscriber
    from apps.subscribers.services import get_subscriber

    try:
        subscriber = get_subscriber(identifier)
    except Subscriber.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Subscriber not found: {identifier}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Subscriber not found: {identifier}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(
            json.dumps(_json_envelope(ok=True, data=_subscriber_to_dict(subscriber)), indent=2)
        )
    else:
        tags = ", ".join(subscriber.tags.values_list("name", flat=True)) or "(none)"
        click.echo(f"ID:         {subscriber.id}")
        click.echo(f"Email:      {subscriber.email}")
        click.echo(f"Name:       {subscriber.name}")
        click.echo(f"Status:     {subscriber.status}")
        click.echo(f"Health:     {subscriber.health_score}")
        click.echo(f"Source:     {subscriber.source}")
        click.echo(f"Tags:       {tags}")
        click.echo(f"Created:    {subscriber.created_at}")
        click.echo(f"Updated:    {subscriber.updated_at}")


@subscribers.command(name="add")
@click.argument("email")
@click.option("--name", default="", help="Subscriber name.")
@click.option("--tag", "tags", multiple=True, help="Tag name (repeatable).")
@click.option(
    "--metadata", "metadata_pairs", multiple=True, help="KEY=VALUE metadata (repeatable)."
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def subscribers_add(
    email: str,
    name: str,
    tags: tuple[str, ...],
    metadata_pairs: tuple[str, ...],
    as_json: bool,
) -> None:
    """Add a new subscriber."""
    _setup_django()
    from apps.subscribers.services import SuppressedSubscriberError, add_subscriber

    parsed_metadata: dict[str, str] = {}
    for pair in metadata_pairs:
        if "=" not in pair:
            if as_json:
                click.echo(
                    json.dumps(
                        _json_envelope(
                            ok=False,
                            error=f"Invalid metadata format: {pair!r} (expected KEY=VALUE)",
                        ),
                        indent=2,
                    ),
                    err=True,
                )
            else:
                click.echo(f"Invalid metadata format: {pair!r} (expected KEY=VALUE)", err=True)
            sys.exit(1)
        key, value = pair.split("=", 1)
        parsed_metadata[key] = value

    tag_names = list(tags) if tags else None

    try:
        subscriber = add_subscriber(
            email=email,
            name=name,
            metadata=parsed_metadata or None,
            tag_names=tag_names,
        )
    except SuppressedSubscriberError as exc:
        if as_json:
            click.echo(
                json.dumps(_json_envelope(ok=False, error=str(exc)), indent=2),
                err=True,
            )
        else:
            click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(
            json.dumps(_json_envelope(ok=True, data=_subscriber_to_dict(subscriber)), indent=2)
        )
    else:
        click.echo(f"Added subscriber {subscriber.email} (status: {subscriber.status})")


@subscribers.command(name="count")
@click.option("--status", default=None, help="Filter by status.")
@click.option("--tag", default=None, help="Filter by tag name.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def subscribers_count(status: str | None, tag: str | None, as_json: bool) -> None:
    """Count subscribers with optional filters."""
    _setup_django()
    from apps.subscribers.services import count_subscribers

    total = count_subscribers(status=status, tag=tag)

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data={"count": total}), indent=2))
    else:
        click.echo(f"{total} subscriber{'s' if total != 1 else ''}")


# ---------------------------------------------------------------------------
# gdpr group
# ---------------------------------------------------------------------------


@main.group()
def gdpr() -> None:
    """GDPR compliance commands."""


@gdpr.command(name="audit")
@click.argument("email")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def gdpr_audit(email: str, as_json: bool) -> None:
    """Show full GDPR audit trail for a subscriber."""
    _setup_django()
    from apps.subscribers.models import Subscriber
    from apps.subscribers.services import export_subscriber_data, get_subscriber

    try:
        subscriber = get_subscriber(email)
    except Subscriber.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Subscriber not found: {email}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Subscriber not found: {email}", err=True)
        sys.exit(1)

    data = export_subscriber_data(subscriber)

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2, default=str))
    else:
        sub = data["subscriber"]
        click.echo("=== Subscriber ===")
        click.echo(f"Email:      {sub['email']}")
        click.echo(f"Name:       {sub['name']}")
        click.echo(f"Status:     {sub['status']}")
        click.echo(f"Source:     {sub['source']}")
        click.echo(f"Created:    {sub['created_at']}")

        click.echo(f"\nTags: {', '.join(data['tags']) or '(none)'}")

        consent_records = data["consent_records"]
        click.echo(f"\n=== Consent Records ({len(consent_records)}) ===")
        for record in consent_records:
            click.echo(
                f"  [{record['created_at']}] {record['action']} via {record['method']}"
            )

        events = data["unsubscribe_events"]
        click.echo(f"\n=== Unsubscribe Events ({len(events)}) ===")
        for event in events:
            click.echo(f"  [{event['created_at']}] {event['method']}")


@gdpr.command(name="export")
@click.argument("email")
@click.option("-o", "--output", default=None, help="Output file path.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def gdpr_export(email: str, output: str | None, as_json: bool) -> None:
    """Export all personal data for a subscriber (JSON)."""
    _setup_django()
    from apps.subscribers.models import Subscriber
    from apps.subscribers.services import export_subscriber_data, get_subscriber

    try:
        subscriber = get_subscriber(email)
    except Subscriber.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Subscriber not found: {email}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Subscriber not found: {email}", err=True)
        sys.exit(1)

    data = export_subscriber_data(subscriber)
    content = json.dumps(data, indent=2, default=str)

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2, default=str))
    elif output:
        from pathlib import Path

        Path(output).write_text(content)
        click.echo(f"Data exported to {output}")
    else:
        click.echo(content)


@gdpr.command(name="delete")
@click.argument("email")
@click.option("--confirm", "confirmed", is_flag=True, help="Confirm deletion.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def gdpr_delete(email: str, confirmed: bool, as_json: bool) -> None:
    """Request GDPR data deletion for a subscriber."""
    if not confirmed:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(
                        ok=False,
                        error="Deletion requires --confirm to proceed.",
                    ),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(
                "WARNING: This will permanently delete personal data for "
                f"{email}. Use --confirm to proceed.",
                err=True,
            )
        sys.exit(1)

    _setup_django()
    from apps.consent.services import process_gdpr_deletion
    from apps.subscribers.models import Subscriber
    from apps.subscribers.services import get_subscriber

    try:
        subscriber = get_subscriber(email)
    except Subscriber.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Subscriber not found: {email}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Subscriber not found: {email}", err=True)
        sys.exit(1)

    process_gdpr_deletion(subscriber, method="gdpr_deletion_cli")

    if as_json:
        click.echo(
            json.dumps(
                _json_envelope(ok=True, data={"email": email, "status": "deleted"}),
                indent=2,
            )
        )
    else:
        click.echo(f"Deleted subscriber {email}")


# ---------------------------------------------------------------------------
# campaigns group
# ---------------------------------------------------------------------------


@main.group()
def campaigns() -> None:
    """Campaign management commands."""


@campaigns.command(name="list")
@click.option(
    "--status", default=None,
    help="Filter by status (draft/scheduled/sending/sent/cancelled).",
)
@click.option("--limit", type=int, default=50, help="Max results.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def campaigns_list(status: str | None, limit: int, as_json: bool) -> None:
    """List campaigns with optional filters."""
    _setup_django()
    from apps.campaigns.models import Campaign

    qs = Campaign.objects.select_related("email_type", "template").all()
    if status:
        qs = qs.filter(status=status)
    qs = qs[:limit]

    campaigns_list = list(qs)

    if as_json:
        data = {
            "campaigns": [_campaign_to_dict(c) for c in campaigns_list],
            "count": len(campaigns_list),
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        if not campaigns_list:
            click.echo("No campaigns found.")
            return
        click.echo(f"{'ID':<6} {'Name':<30} {'Status':<14} {'Template':<20} {'Recipients'}")
        click.echo("-" * 86)
        for c in campaigns_list:
            tmpl = c.template.slug if c.template_id else "-"
            click.echo(
                f"{c.pk:<6} {c.name:<30} {c.status:<14} {tmpl:<20} {c.recipient_count}"
            )
        click.echo(f"\nShowing {len(campaigns_list)} campaign(s)")


@campaigns.command(name="get")
@click.argument("campaign_id", type=int)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def campaigns_get(campaign_id: int, as_json: bool) -> None:
    """Get details for a single campaign."""
    _setup_django()
    from apps.campaigns.models import Campaign

    try:
        campaign = Campaign.objects.select_related("email_type", "template").get(
            pk=campaign_id,
        )
    except Campaign.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Campaign not found: {campaign_id}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Campaign not found: {campaign_id}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=_campaign_to_dict(campaign)), indent=2))
    else:
        click.echo(f"ID:             {campaign.pk}")
        click.echo(f"Name:           {campaign.name}")
        click.echo(f"Status:         {campaign.get_status_display()}")
        click.echo(f"Email Type:     {campaign.email_type}")
        click.echo(f"Template:       {campaign.template}")
        click.echo(f"Subject:        {campaign.subject_line}")
        click.echo(f"Audience:       {json.dumps(campaign.audience_filter)}")
        click.echo(f"Recipients:     {campaign.recipient_count}")
        click.echo(f"Scheduled at:   {campaign.scheduled_at or '-'}")
        click.echo(f"Sent at:        {campaign.sent_at or '-'}")
        click.echo(f"Created:        {campaign.created_at}")
        click.echo(f"Updated:        {campaign.updated_at}")


@campaigns.command(name="create")
@click.option("--name", required=True, help="Campaign name.")
@click.option("--email-type", "email_type_slug", required=True, help="EmailType slug.")
@click.option("--template", "template_slug", required=True, help="EmailTemplate slug.")
@click.option("--subject", "subject_line", required=True, help="Subject line.")
@click.option("--audience-filter", default=None, help="JSON audience filter.")
@click.option("--scheduled-at", default=None, help="ISO datetime for scheduled send.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def campaigns_create(
    name: str,
    email_type_slug: str,
    template_slug: str,
    subject_line: str,
    audience_filter: str | None,
    scheduled_at: str | None,
    as_json: bool,
) -> None:
    """Create a new campaign."""
    _setup_django()
    from apps.campaigns.models import Campaign
    from apps.consent.models import EmailType
    from apps.templates_mgr.models import EmailTemplate

    try:
        email_type = EmailType.objects.get(slug=email_type_slug)
    except EmailType.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"EmailType not found: {email_type_slug}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"EmailType not found: {email_type_slug}", err=True)
        sys.exit(1)

    try:
        template = EmailTemplate.objects.get(slug=template_slug)
    except EmailTemplate.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Template not found: {template_slug}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Template not found: {template_slug}", err=True)
        sys.exit(1)

    parsed_filter: dict = {}
    if audience_filter is not None:
        try:
            parsed_filter = json.loads(audience_filter)
        except json.JSONDecodeError as exc:
            if as_json:
                click.echo(
                    json.dumps(
                        _json_envelope(ok=False, error=f"Invalid audience_filter JSON: {exc}"),
                        indent=2,
                    ),
                    err=True,
                )
            else:
                click.echo(f"Invalid audience_filter JSON: {exc}", err=True)
            sys.exit(1)

        if not isinstance(parsed_filter, dict):
            if as_json:
                click.echo(
                    json.dumps(
                        _json_envelope(
                            ok=False,
                            error="audience_filter must be a JSON object, "
                            f"got {type(parsed_filter).__name__}.",
                        ),
                        indent=2,
                    ),
                    err=True,
                )
            else:
                click.echo(
                    "audience_filter must be a JSON object, "
                    f"got {type(parsed_filter).__name__}.",
                    err=True,
                )
            sys.exit(1)

    parsed_scheduled_at = None
    if scheduled_at is not None:
        try:
            parsed_scheduled_at = datetime.fromisoformat(scheduled_at)
        except (ValueError, TypeError) as exc:
            if as_json:
                click.echo(
                    json.dumps(
                        _json_envelope(ok=False, error=f"Invalid scheduled_at: {exc}"),
                        indent=2,
                    ),
                    err=True,
                )
            else:
                click.echo(f"Invalid scheduled_at: {exc}", err=True)
            sys.exit(1)

        if parsed_scheduled_at.tzinfo is None:
            from django.utils.timezone import make_aware

            parsed_scheduled_at = make_aware(parsed_scheduled_at)

    initial_status = Campaign.Status.SCHEDULED if parsed_scheduled_at else Campaign.Status.DRAFT

    campaign = Campaign.objects.create(
        name=name,
        email_type=email_type,
        template=template,
        subject_line=subject_line,
        status=initial_status,
        audience_filter=parsed_filter,
        scheduled_at=parsed_scheduled_at,
    )

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=_campaign_to_dict(campaign)), indent=2))
    else:
        click.echo(
            f"Created campaign '{campaign.name}' "
            f"(id={campaign.pk}, status={campaign.status})"
        )


@campaigns.command(name="send")
@click.argument("campaign_id", type=int)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def campaigns_send(campaign_id: int, as_json: bool) -> None:
    """Send a campaign to its audience."""
    _setup_django()
    from apps.campaigns.services import CampaignSendError, send_campaign

    try:
        result = send_campaign(campaign_id)
    except CampaignSendError as exc:
        if as_json:
            click.echo(
                json.dumps(_json_envelope(ok=False, error=str(exc)), indent=2),
                err=True,
            )
        else:
            click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if as_json:
        data = {
            "campaign_id": result.campaign_id,
            "campaign_name": result.campaign_name,
            "eligible": result.eligible,
            "sent": result.sent,
            "skipped": result.skipped,
            "failed": result.failed,
            "errors": result.errors,
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo(f"Campaign '{result.campaign_name}' sent.")
        click.echo(f"  Eligible: {result.eligible}")
        click.echo(f"  Sent:     {result.sent}")
        click.echo(f"  Skipped:  {result.skipped}")
        click.echo(f"  Failed:   {result.failed}")
        if result.errors:
            click.echo("  Errors:")
            for err in result.errors:
                click.echo(f"    - {err}")


@campaigns.command(name="send-test")
@click.argument("campaign_id", type=int)
@click.argument("email")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def campaigns_send_test(campaign_id: int, email: str, as_json: bool) -> None:
    """Send a test email for a campaign to the given address."""
    _setup_django()
    from apps.campaigns.services import TestEmailError, send_test_email

    try:
        result = send_test_email(campaign_id, email)
    except (TestEmailError, Exception) as exc:
        if as_json:
            click.echo(
                json.dumps(_json_envelope(ok=False, error=str(exc)), indent=2),
                err=True,
            )
        else:
            click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if as_json:
        data = {
            "recipient": result.recipient,
            "subject": result.subject,
            "provider_message_id": result.provider_message_id,
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo(f"Test email sent to {result.recipient}")
        click.echo(f"  Subject: {result.subject}")
        if result.provider_message_id:
            click.echo(f"  Provider ID: {result.provider_message_id}")


# ---------------------------------------------------------------------------
# templates group
# ---------------------------------------------------------------------------


@main.group()
def templates() -> None:
    """Email template management commands."""


@templates.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def templates_list(as_json: bool) -> None:
    """List all email templates."""
    _setup_django()
    from apps.templates_mgr.models import EmailTemplate

    all_templates = list(EmailTemplate.objects.all())

    if as_json:
        data = {
            "templates": [_template_to_dict(t) for t in all_templates],
            "count": len(all_templates),
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        if not all_templates:
            click.echo("No templates found.")
            return
        click.echo(f"{'ID':<6} {'Name':<30} {'Slug':<20} {'Subject'}")
        click.echo("-" * 80)
        for t in all_templates:
            click.echo(f"{t.pk:<6} {t.name:<30} {t.slug:<20} {t.subject_default}")
        click.echo(f"\nShowing {len(all_templates)} template(s)")


@templates.command(name="get")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def templates_get(slug: str, as_json: bool) -> None:
    """Get details for a single email template."""
    _setup_django()
    from apps.templates_mgr.models import EmailTemplate

    try:
        template = EmailTemplate.objects.get(slug=slug)
    except EmailTemplate.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Template not found: {slug}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Template not found: {slug}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=_template_to_dict(template)), indent=2))
    else:
        click.echo(f"ID:             {template.pk}")
        click.echo(f"Name:           {template.name}")
        click.echo(f"Slug:           {template.slug}")
        click.echo(f"Subject:        {template.subject_default}")
        click.echo(f"HTML body:      {len(template.html_body)} chars")
        click.echo(f"Text body:      {len(template.text_body)} chars")
        click.echo(f"Created:        {template.created_at}")
        click.echo(f"Updated:        {template.updated_at}")


@templates.command(name="create")
@click.option("--name", required=True, help="Template name.")
@click.option("--slug", required=True, help="Template slug (unique identifier).")
@click.option("--subject", "subject_default", required=True, help="Default subject line.")
@click.option("--html-body", required=True, help="HTML body content.")
@click.option("--text-body", default="", help="Plain text body (optional).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def templates_create(
    name: str,
    slug: str,
    subject_default: str,
    html_body: str,
    text_body: str,
    as_json: bool,
) -> None:
    """Create a new email template."""
    _setup_django()
    from django.db import IntegrityError

    from apps.templates_mgr.models import EmailTemplate

    try:
        template = EmailTemplate.objects.create(
            name=name,
            slug=slug,
            subject_default=subject_default,
            html_body=html_body,
            text_body=text_body,
        )
    except IntegrityError:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Template with slug '{slug}' already exists."),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Template with slug '{slug}' already exists.", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=_template_to_dict(template)), indent=2))
    else:
        click.echo(f"Created template '{template.name}' (slug={template.slug})")


# ---------------------------------------------------------------------------
# sequences group
# ---------------------------------------------------------------------------


@main.group()
def sequences() -> None:
    """Sequence management commands."""


@sequences.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def sequences_list(as_json: bool) -> None:
    """List all sequences."""
    _setup_django()
    from apps.campaigns.models import Sequence

    all_sequences = list(Sequence.objects.all())

    if as_json:
        data = {
            "sequences": [_sequence_to_dict(s) for s in all_sequences],
            "count": len(all_sequences),
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        if not all_sequences:
            click.echo("No sequences found.")
            return
        click.echo(f"{'ID':<6} {'Name':<30} {'Slug':<20} {'Trigger':<20} {'Active'}")
        click.echo("-" * 90)
        for s in all_sequences:
            click.echo(
                f"{s.pk:<6} {s.name:<30} {s.slug:<20} "
                f"{s.get_trigger_type_display():<20} {'Yes' if s.is_active else 'No'}"
            )
        click.echo(f"\nShowing {len(all_sequences)} sequence(s)")


@sequences.command(name="status")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def sequences_status(slug: str, as_json: bool) -> None:
    """Show status of a sequence with enrollment counts."""
    _setup_django()
    from apps.campaigns.models import Sequence

    try:
        sequence = Sequence.objects.get(slug=slug)
    except Sequence.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Sequence not found: {slug}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Sequence not found: {slug}", err=True)
        sys.exit(1)

    from django.db.models import Count as _Count

    from apps.campaigns.models import SequenceEnrollment

    enrollment_counts = dict(
        SequenceEnrollment.objects.filter(sequence=sequence)
        .values("status")
        .annotate(cnt=_Count("id"))
        .values_list("status", "cnt"),
    )

    steps = list(sequence.steps.values("order", "delay_hours", "subject_override"))

    if as_json:
        data = _sequence_to_dict(sequence)
        data["enrollment_counts"] = enrollment_counts
        data["steps"] = steps
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo(f"Name:        {sequence.name}")
        click.echo(f"Slug:        {sequence.slug}")
        click.echo(f"Trigger:     {sequence.get_trigger_type_display()}")
        click.echo(f"Active:      {'Yes' if sequence.is_active else 'No'}")
        click.echo(f"Steps:       {len(steps)}")
        click.echo("Enrollments:")
        for status_val, count in enrollment_counts.items():
            click.echo(f"  {status_val}: {count}")


@sequences.command(name="enroll")
@click.argument("identifier")
@click.argument("sequence_slug")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def sequences_enroll(identifier: str, sequence_slug: str, as_json: bool) -> None:
    """Enroll a subscriber in a sequence."""
    _setup_django()
    from apps.campaigns.models import Sequence
    from apps.campaigns.services import EnrollmentError, enroll_subscriber
    from apps.subscribers.models import Subscriber
    from apps.subscribers.services import get_subscriber

    try:
        subscriber = get_subscriber(identifier)
    except Subscriber.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Subscriber not found: {identifier}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Subscriber not found: {identifier}", err=True)
        sys.exit(1)

    try:
        sequence = Sequence.objects.get(slug=sequence_slug)
    except Sequence.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Sequence not found: {sequence_slug}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Sequence not found: {sequence_slug}", err=True)
        sys.exit(1)

    try:
        enrollment = enroll_subscriber(subscriber, sequence)
    except EnrollmentError as exc:
        if as_json:
            click.echo(json.dumps(_json_envelope(ok=False, error=str(exc)), indent=2), err=True)
        else:
            click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if as_json:
        data = {
            "subscriber": subscriber.email,
            "sequence": sequence.slug,
            "status": enrollment.status,
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo(f"Enrolled {subscriber.email} in '{sequence.name}'")


@sequences.command(name="pause")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def sequences_pause(slug: str, as_json: bool) -> None:
    """Pause a sequence and all its active enrollments."""
    _setup_django()
    from apps.campaigns.models import Sequence
    from apps.campaigns.services import pause_sequence

    try:
        sequence = Sequence.objects.get(slug=slug)
    except Sequence.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Sequence not found: {slug}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Sequence not found: {slug}", err=True)
        sys.exit(1)

    count = pause_sequence(sequence)

    if as_json:
        click.echo(
            json.dumps(
                _json_envelope(ok=True, data={"slug": slug, "paused_enrollments": count}),
                indent=2,
            )
        )
    else:
        click.echo(f"Paused '{sequence.name}' ({count} enrollments paused)")


@sequences.command(name="resume")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def sequences_resume(slug: str, as_json: bool) -> None:
    """Resume a paused sequence and its enrollments."""
    _setup_django()
    from apps.campaigns.models import Sequence
    from apps.campaigns.services import resume_sequence

    try:
        sequence = Sequence.objects.get(slug=slug)
    except Sequence.DoesNotExist:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Sequence not found: {slug}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Sequence not found: {slug}", err=True)
        sys.exit(1)

    count = resume_sequence(sequence)

    if as_json:
        click.echo(
            json.dumps(
                _json_envelope(ok=True, data={"slug": slug, "resumed_enrollments": count}),
                indent=2,
            )
        )
    else:
        click.echo(f"Resumed '{sequence.name}' ({count} enrollments reactivated)")


# ---------------------------------------------------------------------------
# analytics group
# ---------------------------------------------------------------------------


@main.group()
def analytics() -> None:
    """Analytics and reporting commands."""


@analytics.command(name="overview")
@click.option("--days", type=int, default=30, help="Number of days to analyze.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def analytics_overview(days: int, as_json: bool) -> None:
    """Show overview metrics for the last N days."""
    _setup_django()
    from apps.analytics.services import get_overview_metrics

    metrics = get_overview_metrics(days=days)

    if as_json:
        data = {
            "total_subscribers": metrics.total_subscribers,
            "active_subscribers": metrics.active_subscribers,
            "emails_sent": metrics.emails_sent,
            "emails_delivered": metrics.emails_delivered,
            "emails_opened": metrics.emails_opened,
            "emails_clicked": metrics.emails_clicked,
            "emails_bounced": metrics.emails_bounced,
            "emails_complained": metrics.emails_complained,
            "avg_health_score": metrics.avg_health_score,
            "churned_count": metrics.churned_count,
            "new_count": metrics.new_count,
            "open_rate": metrics.open_rate,
            "click_rate": metrics.click_rate,
            "bounce_rate": metrics.bounce_rate,
            "churn_rate": metrics.churn_rate,
            "days": days,
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo(f"Overview (last {days} days)")
        click.echo(
            f"  Subscribers:  {metrics.active_subscribers} active"
            f" / {metrics.total_subscribers} total"
        )
        click.echo(f"  New:          +{metrics.new_count}")
        click.echo(f"  Churned:      -{metrics.churned_count} ({metrics.churn_rate}%)")
        click.echo(f"  Emails sent:  {metrics.emails_sent}")
        click.echo(f"  Delivered:    {metrics.emails_delivered}")
        click.echo(f"  Opened:       {metrics.emails_opened} ({metrics.open_rate}%)")
        click.echo(f"  Clicked:      {metrics.emails_clicked} ({metrics.click_rate}%)")
        click.echo(f"  Bounced:      {metrics.emails_bounced} ({metrics.bounce_rate}%)")
        click.echo(f"  Complained:   {metrics.emails_complained}")
        click.echo(f"  Avg health:   {metrics.avg_health_score}")


@analytics.command(name="churn")
@click.option("--days", type=int, default=30, help="Number of days to analyze.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def analytics_churn(days: int, as_json: bool) -> None:
    """Show churn analysis for the last N days."""
    _setup_django()
    from apps.analytics.services import get_churn_metrics, get_health_distribution

    churn = get_churn_metrics(days=days)
    health = get_health_distribution()

    if as_json:
        data = {
            "period_days": churn.period_days,
            "active_at_start": churn.active_at_start,
            "churned_in_period": churn.churned_in_period,
            "new_in_period": churn.new_in_period,
            "net_change": churn.net_change,
            "churn_rate": churn.churn_rate,
            "churned_by_reason": churn.churned_by_reason,
            "health_distribution": {
                "healthy": health.healthy_count,
                "at_risk": health.at_risk_count,
                "critical": health.critical_count,
                "total": health.total,
            },
            "at_risk_count": len(churn.at_risk_subscribers),
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo(f"Churn analysis (last {days} days)")
        click.echo(f"  Active at start: {churn.active_at_start}")
        click.echo(f"  Churned:         {churn.churned_in_period} ({churn.churn_rate}%)")
        click.echo(f"  New:             +{churn.new_in_period}")
        click.echo(f"  Net change:      {churn.net_change:+d}")
        click.echo()
        click.echo("  Churn breakdown:")
        for reason, count in churn.churned_by_reason.items():
            click.echo(f"    {reason}: {count}")
        click.echo()
        click.echo("  Health distribution:")
        click.echo(f"    Healthy:  {health.healthy_count} ({health.healthy_pct}%)")
        click.echo(f"    At-risk:  {health.at_risk_count} ({health.at_risk_pct}%)")
        click.echo(f"    Critical: {health.critical_count} ({health.critical_pct}%)")
        click.echo(f"    At-risk subscribers: {len(churn.at_risk_subscribers)}")


@analytics.command(name="health-report")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def analytics_health_report(as_json: bool) -> None:
    """Show health score distribution and at-risk subscriber summary."""
    _setup_django()
    from apps.analytics.services import get_health_distribution

    health = get_health_distribution()

    if as_json:
        data = {
            "total_active": health.total,
            "healthy": {"count": health.healthy_count, "pct": health.healthy_pct},
            "at_risk": {"count": health.at_risk_count, "pct": health.at_risk_pct},
            "critical": {"count": health.critical_count, "pct": health.critical_pct},
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo("Health score report")
        click.echo(f"  Active subscribers: {health.total}")
        click.echo(f"  Healthy (70-100):   {health.healthy_count} ({health.healthy_pct}%)")
        click.echo(f"  At-risk (40-69):    {health.at_risk_count} ({health.at_risk_pct}%)")
        click.echo(f"  Critical (0-39):    {health.critical_count} ({health.critical_pct}%)")


@analytics.command(name="campaign-stats")
@click.argument("campaign_id", type=int)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def analytics_campaign_stats(campaign_id: int, as_json: bool) -> None:
    """Show delivery and engagement stats for a campaign."""
    _setup_django()
    from apps.analytics.services import get_campaign_stats

    stats = get_campaign_stats(campaign_id)
    if stats is None:
        if as_json:
            click.echo(
                json.dumps(
                    _json_envelope(ok=False, error=f"Campaign not found: {campaign_id}"),
                    indent=2,
                ),
                err=True,
            )
        else:
            click.echo(f"Campaign not found: {campaign_id}", err=True)
        sys.exit(1)

    if as_json:
        data = {
            "campaign_id": stats.campaign_id,
            "campaign_name": stats.campaign_name,
            "status": stats.status,
            "recipient_count": stats.recipient_count,
            "sent_count": stats.sent_count,
            "delivered_count": stats.delivered_count,
            "opened_count": stats.opened_count,
            "clicked_count": stats.clicked_count,
            "bounced_count": stats.bounced_count,
            "complained_count": stats.complained_count,
            "open_rate": stats.open_rate,
            "click_rate": stats.click_rate,
            "bounce_rate": stats.bounce_rate,
        }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo(f"Campaign: {stats.campaign_name}")
        click.echo(f"  Status:      {stats.status}")
        click.echo(f"  Recipients:  {stats.recipient_count}")
        click.echo(f"  Sent:        {stats.sent_count}")
        click.echo(f"  Delivered:   {stats.delivered_count}")
        click.echo(f"  Opened:      {stats.opened_count} ({stats.open_rate}%)")
        click.echo(f"  Clicked:     {stats.clicked_count} ({stats.click_rate}%)")
        click.echo(f"  Bounced:     {stats.bounced_count} ({stats.bounce_rate}%)")
        click.echo(f"  Complained:  {stats.complained_count}")


# ---------------------------------------------------------------------------
# sync group
# ---------------------------------------------------------------------------


@main.group()
def sync() -> None:
    """Data source sync commands."""


@sync.command(name="run")
@click.option("--source", default=None, help="Sync only the named data source.")
@click.option("--dry-run", is_flag=True, help="Preview changes without writing.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def sync_run(source: str | None, dry_run: bool, as_json: bool) -> None:
    """Sync subscribers from configured data sources."""
    _setup_django()
    from io import StringIO

    from django.core.management import call_command

    output = StringIO()
    call_command("sync_source", source=source or "", dry_run=dry_run, stdout=output, stderr=output)
    result_text = output.getvalue()

    if as_json:
        _setup_django()
        from apps.subscribers.models import SyncLog

        latest_log = SyncLog.objects.order_by("-started_at").first()
        data = {"output": result_text.strip()}
        if latest_log:
            data["latest_log"] = {
                "source": latest_log.data_source.name if latest_log.data_source_id else None,
                "status": latest_log.status,
                "new_count": latest_log.new_count,
                "updated_count": latest_log.updated_count,
                "skipped_count": latest_log.skipped_count,
                "suppressed_count": latest_log.suppressed_count,
            }
        click.echo(json.dumps(_json_envelope(ok=True, data=data), indent=2))
    else:
        click.echo(result_text, nl=False)


@sync.command(name="status")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def sync_status(as_json: bool) -> None:
    """Show recent sync run status."""
    _setup_django()
    from apps.subscribers.services import get_sync_log_summary

    logs = get_sync_log_summary()

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data={"logs": logs}), indent=2))
    else:
        if not logs:
            click.echo("No sync logs found.")
            return
        for log in logs:
            click.echo(
                f"[{log['started_at']}] {log['source']}: "
                f"{log['status']} "
                f"(new={log['new_count']}, updated={log['updated_count']}, "
                f"skipped={log['skipped_count']}, suppressed={log['suppressed_count']})"
            )
            if log.get("error_details"):
                click.echo(f"  Errors: {log['error_details']}")


@sync.command(name="sources")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def sync_sources(as_json: bool) -> None:
    """List configured data sources."""
    _setup_django()
    from apps.subscribers.services import get_sync_sources

    sources = get_sync_sources()

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data={"sources": sources}), indent=2))
    else:
        if not sources:
            click.echo("No data sources configured.")
            return
        for src in sources:
            enabled = "enabled" if src.get("enabled", True) else "disabled"
            click.echo(
                f"  {src['name']} ({src['type']}, {enabled}, "
                f"interval={src.get('sync_interval_hours', 6)}h)"
            )
