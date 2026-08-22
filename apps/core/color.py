"""Colour helpers.

The browser builds its --brand-* ramp in static/js/theme.js; the PDF engine
only needs two shades of the same accent, so this is a deliberately tiny
counterpart rather than a second copy of that algorithm.
"""

DEFAULT_ACCENT = "#1f6f8b"


def normalize_hex(value: str, fallback: str = DEFAULT_ACCENT) -> str:
    value = (value or "").strip()
    if not value.startswith("#"):
        value = "#" + value
    if len(value) != 7:
        return fallback
    try:
        int(value[1:], 16)
    except ValueError:
        return fallback
    return value.lower()


def shade(hex_colour: str, amount: float) -> str:
    """Mix towards white (amount > 0) or black (amount < 0)."""
    hex_colour = normalize_hex(hex_colour)
    channels = [int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)]
    target = 255 if amount >= 0 else 0
    ratio = abs(amount)
    mixed = [round(c + (target - c) * ratio) for c in channels]
    return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in mixed)


def readable_on(hex_colour: str) -> str:
    """Black or white text for a given background."""
    hex_colour = normalize_hex(hex_colour)
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(value):
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return "#000000" if luminance > 0.5 else "#ffffff"
