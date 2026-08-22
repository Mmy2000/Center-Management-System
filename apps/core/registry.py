"""Cached policy registry (docs/03 §D.7, TASK-004).

    from apps.core.registry import settings_registry
    settings_registry.get("attendance.late_after_minutes")   # -> 15

Reads go through a single cached dict of overrides, so evaluating a dozen
policies during a QR scan costs zero database queries.

Cache backend matters: LocMemCache is per-process, so a settings change made in
one worker (or in a shell) is invisible to the others until the 300 s TTL
expires. Production therefore runs Redis (see docs/07 §L.3), where the
invalidation on save is shared by every worker.
"""

from typing import Any

from django.core.cache import cache

from . import policies
from .policies import PolicySpec, spec_for

CACHE_KEY = "core:settings:overrides"
CACHE_TTL = 300


class SettingsRegistry:
    def _overrides(self) -> dict[str, Any]:
        data = cache.get(CACHE_KEY)
        if data is None:
            from .models import Setting

            data = dict(Setting.objects.values_list("key", "value"))
            cache.set(CACHE_KEY, data, CACHE_TTL)
        return data

    def get(self, key: str) -> Any:
        """Return the effective value for ``key``.

        Raises ``KeyError`` for an unknown key — a typo must never silently
        resolve to ``None``.
        """
        spec = spec_for(key)
        raw = self._overrides().get(key, None)
        if raw is None:
            return spec.default
        try:
            return spec.coerce(raw)
        except Exception:  # corrupt override — fall back to the safe default
            return spec.default

    def get_many(self, *keys: str) -> dict[str, Any]:
        overrides = self._overrides()
        out = {}
        for key in keys:
            spec = spec_for(key)
            raw = overrides.get(key, None)
            out[key] = spec.default if raw is None else spec.coerce(raw)
        return out

    def all(self) -> dict[str, Any]:
        return {s.key: self.get(s.key) for s in policies.SPECS}

    def all_for_group(self, group: str) -> list[tuple[PolicySpec, Any]]:
        return [(s, self.get(s.key)) for s in policies.specs_for_group(group)]

    def set(self, key: str, value: Any, *, actor=None) -> Any:
        """Validate and persist an override. Returns the stored value."""
        from .models import Setting

        spec = spec_for(key)
        coerced = spec.validate(spec.coerce(value))
        Setting.objects.update_or_create(
            key=key,
            defaults={"value": spec.to_json(coerced), "updated_by": actor},
        )
        self.invalidate()
        return coerced

    def reset(self, key: str) -> None:
        from .models import Setting

        spec_for(key)  # validate the key exists
        Setting.objects.filter(key=key).delete()
        self.invalidate()

    def invalidate(self) -> None:
        cache.delete(CACHE_KEY)


settings_registry = SettingsRegistry()
