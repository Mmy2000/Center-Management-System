"""Tiny CRUD helpers shared by the configuration screens.

Deliberately small: two functions that turn a ModelForm into the JSON envelope.
Anything with real business rules gets its own service instead (docs/README
conventions).
"""

from django.utils.translation import gettext as _

from .http import DomainError


def _field_errors(form) -> dict:
    return {field: [str(e) for e in errors] for field, errors in form.errors.items()}


def list_response(queryset, serializer, *, limit=500) -> dict:
    return {"results": [serializer(obj) for obj in queryset[:limit]]}


def create_object(request, form_class, serializer, *, key="object"):
    form = form_class(request.json)
    if not form.is_valid():
        raise DomainError("ERR_VALIDATION", _("بيانات غير صحيحة"), field_errors=_field_errors(form))
    obj = form.save()
    return {key: serializer(obj)}


def update_object(request, instance, form_class, serializer, *, key="object"):
    """PATCH semantics: only the supplied keys change."""
    data = {}
    for field in form_class.Meta.fields:
        if field in request.json:
            data[field] = request.json[field]
        else:
            current = getattr(instance, field, None)
            if hasattr(current, "all"):  # m2m
                data[field] = list(current.values_list("pk", flat=True))
            elif hasattr(current, "pk"):
                data[field] = current.pk
            else:
                data[field] = current

    form = form_class(data, instance=instance)
    if not form.is_valid():
        raise DomainError("ERR_VALIDATION", _("بيانات غير صحيحة"), field_errors=_field_errors(form))
    obj = form.save()
    return {key: serializer(obj)}
