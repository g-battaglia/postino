"""Renderer for saved EmailTemplate instances.

Takes an :class:`apps.templates_mgr.models.EmailTemplate` and a context
dictionary, renders subject / HTML / text via Django's template engine,
and returns a ``(subject, html, text)`` triple.

The HTML and text bodies from the template record are composed with the
base email layout (``emails/base_email.html`` / ``.txt``) using Django
template inheritance so the unsubscribe footer, branding, and GDPR
compliance are always present.
"""

from __future__ import annotations

from django.template import Context, Template, TemplateSyntaxError

from apps.consent.email_renderer import (
    MissingUnsubscribeURLError,
    _build_base_context,
)
from apps.templates_mgr.models import EmailTemplate


class TemplateRenderError(Exception):
    """Raised when a saved template contains invalid Django template syntax."""


def render_saved_template(
    template: EmailTemplate,
    context: dict | None = None,
    *,
    subject_override: str | None = None,
) -> tuple[str, str, str]:
    """Render a saved EmailTemplate to a (subject, html, text) triple.

    Parameters
    ----------
    template:
        A persisted :class:`EmailTemplate` instance.
    context:
        Variables to interpolate into subject and body. Common keys:
        ``subscriber_name``, ``subscriber_email``, ``unsubscribe_url``,
        ``preferences_url``, etc.
    subject_override:
        If provided, used as the subject line instead of the template's
        ``subject_default``. May itself contain ``{{ }}`` placeholders
        which will be rendered with *context*.

    Returns
    -------
    tuple[str, str, str]
        ``(subject, html_body, text_body)`` ready for sending.

    Raises
    ------
    MissingUnsubscribeURLError
        If ``unsubscribe_url`` is missing or blank in the merged context.
    TemplateRenderError
        If the saved template body contains invalid Django template syntax.
    """
    ctx = _build_base_context(context or {})

    if not ctx.get("unsubscribe_url", "").strip():
        raise MissingUnsubscribeURLError(
            "unsubscribe_url is required and must not be blank. "
            "Every email must include a visible unsubscribe link."
        )

    subject_source = subject_override or template.subject_default
    subject = _render_fragment(subject_source, ctx)

    html_body = template.html_body
    text_body = template.text_body

    html = _render_with_base_layout("emails/base_email.html", html_body, subject, ctx)

    text = _render_with_base_layout("emails/base_email.txt", text_body, subject, ctx)

    return subject, html, text


def _render_with_base_layout(
    base_template_name: str,
    body: str,
    subject: str,
    context: dict,
) -> str:
    """Render *body* inside the base email layout using template inheritance.

    Constructs a temporary template string that ``{% extends %}`` the base
    layout and fills the ``content`` block with the body content.
    """
    if body:
        child_source = (
            f'{{% extends "{base_template_name}" %}}'
            f"{{% block subject %}}{subject}{{% endblock %}}"
            f"{{% block content %}}{body}{{% endblock %}}"
        )
    else:
        child_source = (
            f'{{% extends "{base_template_name}" %}}'
            f"{{% block subject %}}{subject}{{% endblock %}}"
        )

    try:
        tmpl = Template(child_source)
    except TemplateSyntaxError as exc:
        raise TemplateRenderError(
            f"Invalid template syntax: {exc}"
        ) from exc

    return tmpl.render(Context(context))


def _render_fragment(source: str, context: dict) -> str:
    """Render a Django template fragment string with the given context."""
    try:
        template = Template(source)
    except TemplateSyntaxError as exc:
        raise TemplateRenderError(
            f"Invalid template syntax: {exc}"
        ) from exc

    return template.render(Context(context))
