"""The service-layer feature guard (docs/10 §N.8 layer 4, TASK-108).

Layers 1–3 stop a *request*. This one stops everything else: a management
command, a Celery beat job, a future import script, a shell. Those paths never
touch a view, so without this a disabled feature would still run nightly.

It raises ``DomainError``, so an AJAX caller that somehow reaches a service
directly still gets the documented envelope rather than a 500.
"""

from django.utils.translation import gettext as _

from apps.core.http import DomainError

from .resolver import has_feature


def require_feature(*feature_keys: str) -> None:
    """Refuse unless every named feature is enabled for the current center."""
    for key in feature_keys:
        if not has_feature(key):
            raise DomainError(
                "ERR_FEATURE_DISABLED",
                _("هذه الخاصية غير مُفعّلة في هذا الحساب"),
                status=404,
                data={"feature": key},
            )
