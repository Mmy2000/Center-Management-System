"""URLconf for the platform console host (docs/10 §N.10).

Selected by ``TenantResolutionMiddleware`` when a request arrives on
``settings.CONSOLE_HOST``. It is a *separate* URLconf, not a prefix inside
``cms.urls``, and that is the isolation: a tenant host has no route to a console
view at all, so no permission bug, path-traversal trick or proxy
misconfiguration can bridge the two.

Note what is absent: no student pages, no scanner, no payments — and no
``/admin/``, which stays platform-only and is not part of the product.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

from apps.core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # The health checks only — *not* `include("apps.core.urls")`, which would
    # also mount the tenant-facing /settings/ and /audit/ pages here and shadow
    # the console's own. Including a whole app's URLs into this file is exactly
    # the leak the separate URLconf exists to prevent, so routes arrive one at
    # a time and on purpose.
    path("healthz/", core_views.healthz, name="healthz"),
    path("readyz/", core_views.readyz, name="readyz"),
    # ui.js calls gettext() for its empty and error states, so the console needs
    # the same catalog the product loads. Same domain, same .po file.
    path(
        "jsi18n/",
        JavaScriptCatalog.as_view(domain="django"),
        name="javascript-catalog",
    ),
    path("", include("apps.console.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
