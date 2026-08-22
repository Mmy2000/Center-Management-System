"""Settings (policy) API — TASK-011."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .audit import record
from .http import DomainError, ajax
from .models import AuditAction
from .policies import spec_for
from .registry import settings_registry


@ajax(methods=["GET"], perm="core.view_setting")
def settings_list(request):
    from .policies import GROUP_LABELS, GROUPS

    groups = []
    for group in GROUPS:
        groups.append(
            {
                "group": group,
                "label": str(GROUP_LABELS[group]),
                "items": [
                    {
                        "key": spec.key,
                        "label": str(spec.label),
                        "help_text": str(spec.help_text),
                        "type": spec.value_type,
                        "choices": list(spec.choices),
                        "value": value,
                        "default": spec.default,
                    }
                    for spec, value in settings_registry.all_for_group(group)
                ],
            }
        )
    return {"groups": groups}


@ajax(methods=["POST"], perm="core.change_setting")
def settings_update(request):
    """Body: ``{"values": {"<key>": <value>, ...}, "reason": "..."}``."""
    values = request.json.get("values") or {}
    if not isinstance(values, dict) or not values:
        raise DomainError("ERR_VALIDATION", _("لا توجد قيم للحفظ"))

    field_errors: dict[str, list[str]] = {}
    changes: dict[str, dict] = {}

    with transaction.atomic():
        for key, raw in values.items():
            try:
                spec = spec_for(key)
            except KeyError:
                field_errors[key] = [_("مفتاح غير معروف")]
                continue
            if not spec.is_editable:
                field_errors[key] = [_("هذا الإعداد غير قابل للتعديل")]
                continue

            old = settings_registry.get(key)
            try:
                new = settings_registry.set(key, raw, actor=request.user)
            except ValidationError as exc:
                field_errors[key] = [str(m) for m in exc.messages]
                continue
            if old != new:
                changes[key] = {"old": spec.to_json(old), "new": spec.to_json(new)}

        if field_errors:
            raise DomainError("ERR_VALIDATION", _("بعض القيم غير صحيحة"), field_errors=field_errors)

        if changes:
            record(
                AuditAction.SETTING_CHANGED,
                changes=changes,
                reason=request.json.get("reason", ""),
                object_repr=", ".join(sorted(changes)),
            )

    return {"changed": changes, "values": {k: settings_registry.get(k) for k in values}}
