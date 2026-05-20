"""Template utility tags for Postino dashboard.

Provides safe URL resolution and other template helpers.
"""

import json

from django import template
from django.urls import NoReverseMatch, reverse

register = template.Library()


@register.simple_tag(takes_context=False)
def safe_url(name: str, **kwargs) -> str:
    """Resolve a URL pattern name to a path, returning ``#`` on failure.

    Used in sidebar navigation where some app URLs may not be
    registered yet during incremental development.

    Usage::

        {% safe_url "subscribers:list" as subscribers_url %}
        <a href="{{ subscribers_url }}">...</a>
    """
    try:
        return reverse(name, kwargs=kwargs)
    except NoReverseMatch:
        return "#"


@register.filter(name="json_dumps")
def json_dumps(value):
    """Format a Python dict/list as pretty-printed JSON for display."""
    return json.dumps(value, indent=2, sort_keys=True)
