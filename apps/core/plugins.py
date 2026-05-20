"""Plugin system for Postino.

Plugins are Python packages that register via entry points and provide
extra context to email templates. Discovery uses the standard
``importlib.metadata`` entry point group ``postino.plugins``.

A plugin must be both *installed* and *enabled in config.toml* to activate.
If a plugin fails, the error is logged and email sending continues.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class PostinoPlugin(Protocol):
    """Interface every Postino plugin must implement."""

    name: str

    def configure(self, config: dict[str, Any]) -> None: ...

    def enrich_context(self, campaign: Any, subscriber: Any) -> dict[str, Any]: ...


_registry: dict[str, PostinoPlugin] = {}
_configured = False


def discover_and_configure(plugin_configs: dict[str, dict[str, Any]]) -> None:
    """Find installed plugins via entry points and configure those enabled in TOML."""
    global _configured
    _registry.clear()

    for ep in entry_points(group="postino.plugins"):
        toml_section = plugin_configs.get(ep.name, {})
        if not toml_section.get("enabled", False):
            continue

        try:
            cls = ep.load()
            instance = cls()
            instance.configure(toml_section)
            _registry[ep.name] = instance
            logger.info("Plugin '%s' loaded and configured.", ep.name)
        except Exception:
            logger.exception("Failed to load plugin '%s' — skipping.", ep.name)

    _configured = True


def get_plugin_context(campaign: Any, subscriber: Any) -> dict[str, Any]:
    """Collect extra template context from all active plugins."""
    ctx: dict[str, Any] = {}
    for name, plugin in _registry.items():
        try:
            extra = plugin.enrich_context(campaign, subscriber)
            if extra:
                ctx.update(extra)
        except Exception:
            logger.exception(
                "Plugin '%s' failed in enrich_context — skipping.", name,
            )
    return ctx


def get_active_plugins() -> dict[str, PostinoPlugin]:
    """Return the current plugin registry (for inspection/debugging)."""
    return dict(_registry)
