"""The console mounted under a path, for hosting with one hostname.

Used only when ``settings.CONSOLE_PATH_PREFIX`` is set — see
``cms/urls_console.py`` for what that mode costs and why it is opt-in.

Separate from the host URLconf rather than one module branching on a setting:
an install that has both a console hostname *and* a prefix (a free account
that later moved to a paid one, say) must keep working on both, and a single
module can only mount its routes in one place.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from .urls_console import console_patterns

_prefix = (getattr(settings, "CONSOLE_PATH_PREFIX", "") or "").strip("/")

urlpatterns = [path(f"{_prefix}/", include(console_patterns))] if _prefix else []

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
