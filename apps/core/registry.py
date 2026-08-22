"""Cached policy registry (docs/03 §D.7, TASK-004).

    from apps.core.registry import settings_registry
    settings_registry.get("attendance.late_after_minutes")   # -> 15

Reads go through a single cached dict of overrides, so evaluating a dozen
policies during a QR scan costs zero database queries.

Cache backend matters: LocMemCache is per-process, so a settings change made in
one worker (or in a shell) is invisible to the others until the 300 s TTL
expires. Production therefore runs Redis (see docs/07 §L.3), where the
invalidation on save is shared by every worker.

Every key is namespaced by tenant (docs/10 §N.7). This is not a nicety: a shared
cache key would serve one center's fees to another, and no queryset filter would
catch it. Reading another tenant's settings is possible only inside
``tenant_context(other)``, which also gives that read the right cache key.
"""

from typing import Any

from django.core.cache import cache

from apps.tenancy.context import require_tenant

from . import policies
from .policies import PolicySpec, spec_for

CACHE_KEY_PREFIX = "core:settings:"
CACHE_TTL = 300


def cache_key(tenant_id: int) -> str:
    return f"{CACHE_KEY_PREFIX}{tenant_id}:overrides"


class SettingsRegistry:
    def _overrides(self) -> dict[str, Any]:
        key = cache_key(require_tenant().pk)
        data = cache.get(key)
        if data is None:
            from .models import Setting

            data = dict(Setting.objects.values_list("key", "value"))
            cache.set(key, data, CACHE_TTL)
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
        cache.delete(cache_key(require_tenant().pk))


settings_registry = SettingsRegistry()
