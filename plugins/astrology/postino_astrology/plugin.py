"""Astrology plugin for Postino.

Adds current sky data (planetary positions, moon phase, retrogrades)
to the email template context via the Astrologer API.

Get your API key at: https://www.kerykeion.net/astrologer-api/subscribe

Templates access the data as ``{{ sky.planets }}``, ``{{ sky.moon_phase }}``, etc.
If the plugin is not installed or not configured, those variables are simply absent
and ``{% if sky %}`` blocks are skipped.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

from postino_astrology.client import fetch_sky_data

logger = logging.getLogger(__name__)


class AstrologyPlugin:
    """Postino plugin that enriches email context with astrological data."""

    name = "astrology"

    def __init__(self) -> None:
        self.api_key: str = ""
        self.api_key_header: str = "X-RapidAPI-Key"
        self.api_url: str = ""
        self.default_latitude: float = 0.0
        self.default_longitude: float = 0.0
        self._cache: dict[str, Any] | None = None
        self._cache_date: str = ""

    def configure(self, config: dict[str, Any]) -> None:
        self.api_key = config.get("api_key", "")
        self.api_key_header = config.get("api_key_header", "X-RapidAPI-Key")
        self.api_url = config.get("api_url", "")
        self.default_latitude = config.get("default_latitude", 0.0)
        self.default_longitude = config.get("default_longitude", 0.0)

    def enrich_context(self, campaign: Any, subscriber: Any) -> dict[str, Any]:
        """Return ``{"sky": ...}`` with current planetary data.

        Results are cached per UTC date so we don't call the API once
        per subscriber in a bulk send.
        """
        if not self.api_key:
            return {}

        from datetime import datetime

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if self._cache and self._cache_date == today:
            return {"sky": self._cache}

        sky = fetch_sky_data(
            api_key=self.api_key,
            api_key_header=self.api_key_header,
            api_url=self.api_url,
            latitude=self.default_latitude,
            longitude=self.default_longitude,
        )
        if sky is None:
            return {}

        self._cache = sky
        self._cache_date = today
        return {"sky": sky}
