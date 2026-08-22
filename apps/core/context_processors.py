"""Template context shared by every page."""

from .registry import settings_registry


def branding(request):
    """Center identity and appearance defaults.

    The appearance values are only *defaults*: theme.js layers each user's own
    choice (localStorage) on top before the first paint.
    """
    return {
        "CENTER_NAME": settings_registry.get("center.name"),
        "CENTER_PHONE": settings_registry.get("center.phone"),
        "CENTER_ADDRESS": settings_registry.get("center.address"),
        "UI_THEME": settings_registry.get("ui.theme"),
        "UI_ACCENT": settings_registry.get("ui.accent"),
        "UI_MODE": settings_registry.get("ui.mode"),
        "UI_DENSITY": settings_registry.get("ui.density"),
    }
