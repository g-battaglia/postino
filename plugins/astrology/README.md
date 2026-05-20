# postino-astrology

Astrology content plugin for [Postino](../../README.md). Adds real-time planetary positions, moon phases, and retrogrades to your email templates — powered by the [Astrologer API](https://github.com/g-battaglia/Astrologer-API).

## Get an API key

1. Go to [kerykeion.net/astrologer-api/subscribe](https://www.kerykeion.net/astrologer-api/subscribe)
2. Subscribe on RapidAPI (free tier available)
3. Copy your `X-RapidAPI-Key`

## Install

From the Postino project root:

```bash
pip install -e plugins/astrology
```

## Configure

Add to your `config.toml`:

```toml
[plugins.astrology]
enabled = true
api_key = "your-rapidapi-key-here"
```

That's it. The plugin uses the public RapidAPI endpoint by default.

### Optional settings

```toml
[plugins.astrology]
enabled = true
api_key = "your-rapidapi-key-here"

# Override location for moon phase calculations (default: Greenwich)
default_latitude = 41.9028    # Rome
default_longitude = 12.4964

# API endpoint (defaults to RapidAPI — no need to change):
# api_url = "https://astrologer.p.rapidapi.com/api/v5"
```

## Usage in email templates

The plugin injects a `sky` variable into every email template context:

```html
{% if sky %}
  <p>{{ sky.moon_phase.emoji }} {{ sky.moon_phase.phase_name }}</p>

  <h3>Current Positions</h3>
  {% for planet in sky.planets %}
    <li>{{ planet.symbol }} {{ planet.name }} in {{ planet.sign }}
        {% if planet.retrograde %}℞{% endif %}</li>
  {% endfor %}

  {% if sky.retrograde_planets %}
    <p>Retrogrades:
    {% for p in sky.retrograde_planets %}
      {{ p.symbol }} {{ p.name }}{% if not forloop.last %}, {% endif %}
    {% endfor %}
    </p>
  {% endif %}
{% endif %}
```

If the plugin is not installed or disabled, `sky` is simply not defined — `{% if sky %}` blocks are skipped and emails work normally without astrology content.

## Weekly Sky Briefing command

Generate and schedule a weekly campaign automatically:

```bash
python manage.py generate_weekly_sky          # schedules for next Monday 07:00 UTC
python manage.py generate_weekly_sky --send-now  # schedules immediately
```

Requires:
- A template with slug `weekly-sky-briefing`
- An email type with slug `weekly-sky`

Add to your crontab for full automation:

```cron
0 6 * * 1  cd /app && python manage.py generate_weekly_sky
```

## Template context reference

```python
{
    "sky": {
        "week_label": "May 19 – May 25, 2026",
        "planets": [
            {"name": "Sun", "symbol": "☉", "sign": "Gemini", "retrograde": False, "key": "sun"},
            {"name": "Moon", "symbol": "☽", "sign": "Leo", "retrograde": False, "key": "moon"},
            {"name": "Pluto", "symbol": "♇", "sign": "Aquarius", "retrograde": True, "key": "pluto"},
            # ... 10 planets total
        ],
        "retrograde_planets": [
            {"name": "Pluto", "symbol": "♇", "sign": "Aquarius", "retrograde": True},
        ],
        "moon_phase": {
            "phase_name": "Waxing Crescent",
            "emoji": "🌒",
            "illumination": 23,
            "next_full_moon": "2026-06-03",
            "next_new_moon": "2026-06-18",
        },
        "generated_at_utc": "2026-05-21T08:30:00+00:00",
    }
}
```
