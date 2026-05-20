"""Email template renderer for Postino.

Renders both HTML and plain-text versions of an email template using Django's
template engine. Every call injects the standard context variables (branding,
GDPR footer data, unsubscribe URL) so that child templates always have access
to the base layout variables.

Usage::

    html, text = render_email("emails/welcome.html", {
        "subscriber_name": "Ada",
        "unsubscribe_url": "https://example.com/unsubscribe/abc123",
    })
"""

from __future__ import annotations

from django.conf import settings
from django.template.loader import render_to_string


def _build_base_context(context: dict) -> dict:
    """Merge caller-supplied context with the standard email variables.

    Caller values take precedence over defaults, which allows tests and
    edge cases to override anything.
    """
    defaults = {
        "app_name": getattr(settings, "POSTINO_APP_NAME", "Postino"),
        "primary_color": getattr(settings, "POSTINO_PRIMARY_COLOR", "#6366f1"),
        "physical_address": getattr(settings, "POSTINO_PHYSICAL_ADDRESS", ""),
        "logo_url": getattr(settings, "POSTINO_LOGO_URL", ""),
        "subscriber_name": "",
        "unsubscribe_url": "",
    }
    defaults.update(context)
    return defaults


class MissingUnsubscribeURLError(Exception):
    """Raised when render_email() is called without a valid unsubscribe URL."""


def render_email(template_name: str, context: dict | None = None) -> tuple[str, str]:
    """Render an email template to HTML and plain-text strings.

    Parameters
    ----------
    template_name:
        Path relative to the templates directory, e.g. ``"emails/welcome.html"``.
        The corresponding ``.txt`` variant is resolved by replacing the
        ``.html`` extension with ``.txt``.
    context:
        Optional dict of template variables. Merged with the standard set
        (app_name, primary_color, physical_address, etc.).

    Returns
    -------
    tuple[str, str]
        ``(html_body, text_body)`` — the rendered HTML and plain-text versions.

    Raises
    ------
    MissingUnsubscribeURLError
        If ``unsubscribe_url`` is missing or blank in the merged context.
        Every email must have a visible unsubscribe link.
    """
    ctx = _build_base_context(context or {})

    if not ctx.get("unsubscribe_url", "").strip():
        raise MissingUnsubscribeURLError(
            "unsubscribe_url is required and must not be blank. "
            "Every email must include a visible unsubscribe link."
        )

    html = render_to_string(template_name, ctx)

    text_template = template_name.rsplit(".html", 1)[0] + ".txt"
    text = render_to_string(text_template, ctx)

    return html, text
