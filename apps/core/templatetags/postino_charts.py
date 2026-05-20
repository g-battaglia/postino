"""SVG chart template tags for Postino dashboard.

Generates pure server-side SVG charts for:
  - Sparklines (inline mini-trend in stat cards)
  - Growth area chart (subscriber growth, new vs churned)
  - Stacked bar chart (send volume)
  - Progress ring (health score gauge)

All output is safe, sanitized SVG markup.
"""

import html
import math
from typing import Any

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _escape(value: Any) -> str:
    return html.escape(str(value))


@register.simple_tag(takes_context=False)
def chart_sparkline(
    data: str | list[float],
    color: str = "#6366f1",
    height: int = 28,
    fill: bool = True,
) -> str:
    """Render a sparkline SVG from comma-separated or list data.

    Usage::

        {% load postino_charts %}
        {% chart_sparkline "280,300,312,334,351,371,390,412" color="#10b981" %}
    """
    if isinstance(data, str):
        try:
            vals = [float(v.strip()) for v in data.split(",") if v.strip()]
        except (ValueError, TypeError):
            vals = []
    else:
        vals = [float(v) for v in data]

    if len(vals) < 2:
        return mark_safe("")

    w = 100
    h = height
    max_v = max(vals)
    min_v = min(vals)
    rng = max_v - min_v or 1
    step = w / (len(vals) - 1)

    pts = []
    for i, v in enumerate(vals):
        x = i * step
        y = h - ((v - min_v) / rng) * (h - 4) - 2
        pts.append((x, y))

    path_d = "M" + " L".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts)
    area_d = path_d + f" L{w},{h} L0,{h} Z"

    safe_color = _escape(color)
    svg_style = f"width:100%;height:{h}px"
    parts = [f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="{svg_style}">']
    if fill:
        parts.append(f'<path d="{area_d}" fill="{safe_color}" fill-opacity="0.10"/>')
    parts.append(f'<path d="{path_d}" fill="none" stroke="{safe_color}" stroke-width="1.5"/>')
    parts.append("</svg>")

    return mark_safe("\n".join(parts))


@register.simple_tag(takes_context=False)
def chart_growth(
    data: str | list[dict],
    width: int = 640,
    height: int = 260,
) -> str:
    """Render a growth area chart (new vs churned subscribers).

    ``data`` is a list of dicts ``{"month": "Jan", "new": 420, "churned": 80}``
    or a comma-separated string of ``new1,churned1,new2,churned2,...``.

    Usage::

        {% load postino_charts %}
        {% chart_growth growth_data %}
    """
    months = _parse_growth_data(data)
    if not months or len(months) < 2:
        return mark_safe('<div class="py-8 text-center text-gray-400 text-[13px]">No data</div>')

    W, H = width, height
    P = {"t": 24, "r": 24, "b": 36, "l": 40}
    inner_w = W - P["l"] - P["r"]
    inner_h = H - P["t"] - P["b"]

    max_v = max(max(m["new"], m["churned"]) for m in months) * 1.15
    if max_v == 0:
        max_v = 1

    xs = [P["l"] + (inner_w / (len(months) - 1)) * i for i in range(len(months))]

    def y(v: float) -> float:
        return P["t"] + inner_h - (v / max_v) * inner_h

    def make_path(pts: list[tuple[float, float]]) -> str:
        return "M" + " L".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts)

    def make_area(pts: list[tuple[float, float]]) -> str:
        bottom = P["t"] + inner_h
        return (
            make_path(pts)
            + f" L{pts[-1][0]:.1f},{bottom:.1f} L{pts[0][0]:.1f},{bottom:.1f} Z"
        )

    new_pts = [(xs[i], y(m["new"])) for i, m in enumerate(months)]
    churn_pts = [(xs[i], y(m["churned"])) for i, m in enumerate(months)]

    ticks = [0, round(max_v / 3), round(max_v * 2 / 3), round(max_v)]

    parts = [
        f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto;display:block">'
    ]

    # Grid lines and tick labels
    for i, t in enumerate(ticks):
        yt = y(t)
        dash = "" if i == 0 else ' stroke-dasharray="3 3"'
        parts.append(
            f'<line x1="{P["l"]}" y1="{yt:.1f}" x2="{W-P["r"]}" y2="{yt:.1f}"'
            f' stroke="#f3f4f6"{dash}/>'
        )
        parts.append(
            f'<text x="{P["l"]-8}" y="{yt+4:.1f}" font-size="10.5" fill="#9ca3af"'
            f' text-anchor="end" font-family="JetBrains Mono,ui-monospace,monospace">'
            f"{t}</text>"
        )

    # Month labels
    for i, m in enumerate(months):
        parts.append(
            f'<text x="{xs[i]:.1f}" y="{H-12}" font-size="11" fill="#6b7280"'
            f' text-anchor="middle">{_escape(m["month"])}</text>'
        )

    # Gradient definitions
    parts.append('<defs>')
    parts.append(
        '<linearGradient id="newGrad" x1="0" x2="0" y1="0" y2="1">'
        '<stop offset="0%" stop-color="#10b981" stop-opacity="0.32"/>'
        '<stop offset="100%" stop-color="#10b981" stop-opacity="0"/>'
        "</linearGradient>"
    )
    parts.append(
        '<linearGradient id="churnGrad" x1="0" x2="0" y1="0" y2="1">'
        '<stop offset="0%" stop-color="#ef4444" stop-opacity="0.22"/>'
        '<stop offset="100%" stop-color="#ef4444" stop-opacity="0"/>'
        "</linearGradient>"
    )
    parts.append("</defs>")

    # Areas and lines
    parts.append(f'<path d="{make_area(new_pts)}" fill="url(#newGrad)"/>')
    parts.append(f'<path d="{make_path(new_pts)}" fill="none" stroke="#10b981" stroke-width="2"/>')
    parts.append(f'<path d="{make_area(churn_pts)}" fill="url(#churnGrad)"/>')
    churn_line = f'<path d="{make_path(churn_pts)}" fill="none" stroke="#ef4444" stroke-width="2"/>'
    parts.append(churn_line)

    for p in new_pts:
        parts.append(
            f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="3"'
            f' fill="white" stroke="#10b981" stroke-width="1.5"/>'
        )
    for p in churn_pts:
        parts.append(
            f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="3"'
            f' fill="white" stroke="#ef4444" stroke-width="1.5"/>'
        )

    parts.append("</svg>")
    return mark_safe("\n".join(parts))


@register.simple_tag(takes_context=False)
def chart_bar(
    data: str | list[dict],
    width: int = 640,
    height: int = 240,
) -> str:
    """Render a stacked bar chart (send volume).

    ``data`` is a list of dicts ``{"label": "1d", "delivered": 120, "bounced": 3, "other": 0}``.

    Usage::

        {% load postino_charts %}
        {% chart_bar volume_data %}
    """
    days = _parse_bar_data(data)
    if not days:
        return mark_safe('<div class="py-8 text-center text-gray-400 text-[13px]">No data</div>')

    W, H = width, height
    P = {"t": 16, "r": 12, "b": 32, "l": 36}
    inner_w = W - P["l"] - P["r"]
    inner_h = H - P["t"] - P["b"]

    totals = [d["delivered"] + d["bounced"] + d["other"] for d in days]
    max_v = max(totals) * 1.1 if max(totals) > 0 else 1
    bw = (inner_w / len(days)) - 3

    def y(v: float) -> float:
        return P["t"] + inner_h - (v / max_v) * inner_h

    tick_vals = [0, round(max_v / 2), round(max_v)]

    parts = [f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto">']

    for _i, t in enumerate(tick_vals):
        yt = y(t)
        x1, x2 = P["l"], W - P["r"]
        parts.append(f'<line x1="{x1}" x2="{x2}" y1="{yt:.1f}" y2="{yt:.1f}" stroke="#f3f4f6"/>')
        parts.append(
            f'<text x="{P["l"]-8}" y="{yt+4:.1f}" font-size="10.5" fill="#9ca3af"'
            f' text-anchor="end" font-family="JetBrains Mono,ui-monospace,monospace">'
            f"{t}</text>"
        )

    segments = [
        ("delivered", "#10b981"),
        ("bounced", "#ef4444"),
        ("other", "#d1d5db"),
    ]

    for i, d in enumerate(days):
        x = P["l"] + i * (inner_w / len(days)) + 1.5
        total = d["delivered"] + d["bounced"] + d["other"]
        if total == 0:
            empty_bar = f'<rect x="{x:.1f}" y="{y(0)-2:.1f}" width="{bw:.1f}" height="2"'
            parts.append(f'{empty_bar} fill="#f3f4f6" rx="1"/>')
            continue
        yb = P["t"] + inner_h
        for key, color in segments:
            v = d[key]
            if not v:
                continue
            h = (v / max_v) * inner_h
            yb -= h
            parts.append(
                f'<rect x="{x:.1f}" y="{yb:.1f}" width="{bw:.1f}" height="{h:.1f}"'
                f' fill="{color}" rx="1"/>'
            )

    # Labels (every ~7th or first)
    label_indices = _spread_labels(len(days), max_labels=6)
    for i in label_indices:
        x = P["l"] + i * (inner_w / len(days)) + bw / 2
        parts.append(
            f'<text x="{x:.1f}" y="{H-12}" font-size="10.5" fill="#9ca3af"'
            f' text-anchor="middle" font-family="JetBrains Mono,ui-monospace,monospace">'
            f"{_escape(days[i].get('label', str(i)))}</text>"
        )

    parts.append("</svg>")
    return mark_safe("\n".join(parts))


@register.simple_tag(takes_context=False)
def chart_progress_ring(
    score: int,
    size: int = 96,
    label: str = "Health",
) -> str:
    """Render a circular progress ring (health score gauge).

    Usage::

        {% load postino_charts %}
        {% chart_progress_ring 74 size=96 label="Health" %}
    """
    r = size / 2 - 8
    c = 2 * math.pi * r
    pct = score / 100

    if score >= 70:
        color = "#10b981"
    elif score >= 40:
        color = "#f59e0b"
    else:
        color = "#ef4444"

    cx = size / 2
    cy = size / 2

    parts = [
        f'<div class="relative" style="width:{size}px;height:{size}px">',
        f'<svg width="{size}" height="{size}">',
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#f3f4f6" stroke-width="8"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="8"'
        f' stroke-dasharray="{c:.2f}" stroke-dashoffset="{c*(1-pct):.2f}"'
        f' stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>',
        "</svg>",
        '<div class="absolute inset-0 flex flex-col items-center justify-center">',
        f'<div class="text-[22px] font-semibold tracking-tight tabular-nums">{score}</div>',
        f'<div class="text-[10.5px] text-gray-500 uppercase '
        f'tracking-[0.06em]">{_escape(label)}</div>',
        "</div>",
        "</div>",
    ]
    return mark_safe("\n".join(parts))


def _parse_growth_data(data: Any) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, str) and data:
        try:
            parts = [float(v.strip()) for v in data.split(",") if v.strip()]
            if len(parts) < 2 or len(parts) % 2 != 0:
                return []
            months = []
            month_names = [
                "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
            ]
            for i in range(0, len(parts), 2):
                idx = i // 2
                months.append({
                    "month": month_names[idx % 12],
                    "new": int(parts[i]),
                    "churned": int(parts[i + 1]),
                })
            return months
        except (ValueError, TypeError):
            return []
    return []


def _parse_bar_data(data: Any) -> list[dict]:
    if isinstance(data, list):
        return data
    return []


def _spread_labels(total: int, max_labels: int = 6) -> list[int]:
    if total <= max_labels:
        return list(range(total))
    step = total / max_labels
    return [min(round(i * step), total - 1) for i in range(max_labels)]
