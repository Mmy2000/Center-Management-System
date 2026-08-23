"""Give a client their data back (docs/10 §N.10, TASK-116).

A zip holding ``manifest.json``, ``data.json`` and the center's media. Complete
by construction: the model list comes from the registry, so a model added next
month is exported the day it is added — and the leak suite fails if it is not.

Streamed into a temporary file rather than built in memory: a center with two
thousand students and their photos is not something to hold in a web worker's
heap.
"""

import json
import tempfile
import zipfile
from pathlib import Path

from django.core.serializers import serialize
from django.http import FileResponse
from django.utils import timezone

from apps.tenancy.base import TenantOwnedModel
from apps.tenancy.constants import PlatformAction
from apps.tenancy.context import tenant_context
from apps.tenancy.platform_audit import record

#: Models carrying a tenant that are *not* TenantOwnedModel, and so are not in
#: the registry sweep. Both are nullable-tenant by design (docs/10 §N.5).
EXTRA_MODELS = [("core", "AuditLog"), ("accounts", "User")]


def exportable_models():
    """Every tenant-owned model, from the registry — never a hand-kept list."""
    from django.apps import apps as django_apps

    models = [m for m in django_apps.get_models() if issubclass(m, TenantOwnedModel)]
    for app_label, model_name in EXTRA_MODELS:
        models.append(django_apps.get_model(app_label, model_name))
    return sorted(models, key=lambda m: m._meta.label_lower)


def collect(tenant) -> tuple[dict, str]:
    """``(manifest, serialized_json)`` for one center."""
    rows = []
    counts = {}
    for model in exportable_models():
        manager = getattr(model, "all_tenants", model._base_manager)
        queryset = manager.filter(tenant=tenant)
        counts[model._meta.label_lower] = queryset.count()
        rows.extend(queryset)

    manifest = {
        "tenant": {
            "slug": tenant.slug,
            "name": tenant.name,
            "plan": tenant.plan.slug,
            "status": tenant.status,
        },
        "exported_at": timezone.now().isoformat(),
        "schema_version": 1,
        "counts": counts,
        "total_rows": sum(counts.values()),
    }
    # natural keys off: the importer creates a *new* tenant, and natural keys
    # would try to resolve against the source installation's rows.
    return manifest, serialize("json", rows, indent=1)


def media_root_for(tenant) -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "t" / str(tenant.pk)


def build_archive(tenant) -> Path:
    """Write the zip to a temp file and return its path."""
    with tenant_context(tenant):
        manifest, data = collect(tenant)

    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    handle.close()
    path = Path(handle.name)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("data.json", data)
        media = media_root_for(tenant)
        if media.exists():
            for item in media.rglob("*"):
                if item.is_file():
                    archive.write(item, f"media/{item.relative_to(media).as_posix()}")
    return path


def export_response(tenant, *, actor=None) -> FileResponse:
    path = build_archive(tenant)
    record(
        PlatformAction.TENANT_EXPORTED,
        tenant=tenant,
        actor=actor,
        changes={"bytes": path.stat().st_size},
    )
    stamp = timezone.now().strftime("%Y%m%d-%H%M")
    response = FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=f"{tenant.slug}-{stamp}.zip",
        content_type="application/zip",
    )
    return response
