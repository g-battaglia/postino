"""Astrologer API client for Postino.

Fetches current planetary positions and moon phase data, then structures
it for use in email templates.

Supports two auth modes:
  - **RapidAPI** (default, for external users): X-RapidAPI-Key + X-RapidAPI-Host
  - **Direct** (for self-hosted/internal): custom header name + key

Subscribe to the API: https://www.kerykeion.net/astrologer-api/subscribe

Endpoints used:
  POST {base_url}/now/subject   — current sky (planets, signs, retrogrades)
  POST {base_url}/moon-phase    — moon phase, illumination, upcoming phases
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "astrologer.p.rapidapi.com"
RAPIDAPI_BASE_URL = f"https://{RAPIDAPI_HOST}/api/v5"

PLANET_ORDER = [
    "sun", "moon", "mercury", "venus", "mars",
    "jupiter", "saturn", "uranus", "neptune", "pluto",
]

PLANET_SYMBOLS = {
    "sun": "☉", "moon": "☽", "mercury": "☿", "venus": "♀", "mars": "♂",
    "jupiter": "♃", "saturn": "♄", "uranus": "♅", "neptune": "♆", "pluto": "♇",
}

PLANET_NAMES = {
    "sun": "Sun", "moon": "Moon", "mercury": "Mercury", "venus": "Venus",
    "mars": "Mars", "jupiter": "Jupiter", "saturn": "Saturn",
    "uranus": "Uranus", "neptune": "Neptune", "pluto": "Pluto",
}

SIGN_NAMES = {
    "Ari": "Aries", "Tau": "Taurus", "Gem": "Gemini", "Can": "Cancer",
    "Leo": "Leo", "Vir": "Virgo", "Lib": "Libra", "Sco": "Scorpio",
    "Sag": "Sagittarius", "Cap": "Capricorn", "Aqu": "Aquarius", "Pis": "Pisces",
    "Aries": "Aries", "Taurus": "Taurus", "Gemini": "Gemini", "Cancer": "Cancer",
    "Virgo": "Virgo", "Libra": "Libra", "Scorpio": "Scorpio",
    "Sagittarius": "Sagittarius", "Capricorn": "Capricorn",
    "Aquarius": "Aquarius", "Pisces": "Pisces",
}


def _normalize_sign(sign: str | None) -> str:
    if not sign:
        return ""
    return SIGN_NAMES.get(sign, sign)


def _is_retrograde(planet_data: dict) -> bool:
    if planet_data.get("retrograde") is True:
        return True
    speed = planet_data.get("speed")
    if isinstance(speed, (int, float)) and speed < 0:
        return True
    return False


def _extract_planets(response_data: dict) -> list[dict]:
    subject = response_data.get("subject") or response_data.get("data") or response_data
    planets: list[dict] = []
    for key in PLANET_ORDER:
        planet = subject.get(key)
        if not isinstance(planet, dict):
            continue
        planets.append({
            "key": key,
            "name": PLANET_NAMES.get(key, key.title()),
            "symbol": PLANET_SYMBOLS.get(key, ""),
            "sign": _normalize_sign(planet.get("sign")),
            "retrograde": _is_retrograde(planet),
            "abs_pos": planet.get("abs_pos") or planet.get("position"),
        })
    return planets


def _extract_moon_phase(moon_data: dict) -> dict:
    overview = (
        moon_data.get("moon_phase_overview")
        or moon_data.get("data")
        or moon_data
    )
    return {
        "phase_name": overview.get("moon_phase_name") or overview.get("phase_name") or "",
        "emoji": overview.get("moon_emoji") or overview.get("emoji") or "🌙",
        "illumination": overview.get("illumination"),
        "next_full_moon": overview.get("next_full_moon"),
        "next_new_moon": overview.get("next_new_moon"),
    }


def _build_headers(api_key: str, api_key_header: str) -> dict[str, str]:
    """Build auth headers — RapidAPI or custom direct endpoint."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key_header == "X-RapidAPI-Key":
        headers["X-RapidAPI-Key"] = api_key
        headers["X-RapidAPI-Host"] = RAPIDAPI_HOST
    else:
        headers[api_key_header] = api_key
    return headers


def fetch_sky_data(
    *,
    api_key: str,
    api_key_header: str = "X-RapidAPI-Key",
    api_url: str = "",
    latitude: float = 0.0,
    longitude: float = 0.0,
) -> dict | None:
    """Fetch current planetary positions and moon phase from the Astrologer API.

    By default uses the public RapidAPI endpoint. Set ``api_url`` to override
    with a self-hosted or internal endpoint.

    Returns a dict ready for email template context, or None on failure.
    """
    base_url = api_url.rstrip("/") if api_url else RAPIDAPI_BASE_URL
    headers = _build_headers(api_key, api_key_header)

    now = datetime.now(UTC)

    moon_payload = {
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "UTC",
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            now_resp = client.post(
                f"{base_url}/now/subject",
                json={},
                headers=headers,
            )
            now_resp.raise_for_status()
            now_data = now_resp.json()

            moon_resp = client.post(
                f"{base_url}/moon-phase",
                json=moon_payload,
                headers=headers,
            )
            moon_resp.raise_for_status()
            moon_data = moon_resp.json()
    except httpx.HTTPError:
        logger.exception("Failed to fetch sky data from Astrologer API")
        return None
    except Exception:
        logger.exception("Unexpected error fetching sky data")
        return None

    planets = _extract_planets(now_data)
    retrogrades = [p for p in planets if p["retrograde"]]
    moon_phase = _extract_moon_phase(moon_data)

    week_start = now - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=6)

    return {
        "week_start": week_start.strftime("%B %d"),
        "week_end": week_end.strftime("%B %d, %Y"),
        "week_label": f"{week_start.strftime('%B %d')} – {week_end.strftime('%B %d, %Y')}",
        "planets": planets,
        "retrograde_planets": retrogrades,
        "moon_phase": moon_phase,
        "generated_at_utc": now.isoformat(),
    }
