"""URLconf for the platform console (docs/10 §N.10).

Selected by ``TenantResolutionMiddleware``, by one of two routes:

* **By host** (the default, and the only one for production). A request on
  ``settings.CONSOLE_HOST`` gets this URLconf instead of ``cms.urls``. That is
  the isolation: a tenant host has no route to a console view *at all*, so no
  permission bug, path-traversal trick or proxy mistake can bridge the two.

* **By path**, when ``settings.CONSOLE_PATH_PREFIX`` is set. This exists for
  hosting that gives you exactly one hostname — PythonAnywhere's free tier is
  the case in hand — where there is no second host to put the console on. The
  URLconf is still separate, but the two now share an origin, so the structural
  wall is gone and only ``platform_staff_required`` remains. That is one wall
  instead of two: acceptable for a single-center install you run yourself, not
  for a platform serving other people's data. Leave it empty in production.

Note what is absent either way: no student pages, no scanner, no payments — and
``/admin/`` lives here rather than on a tenant host.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

from apps.core import views as core_views

#: Everything the console serves, mounted at the root by default and under
#: CONSOLE_PATH_PREFIX when there is only one hostname to go round. Reversing
#: works unchanged in both modes because the prefix is part of the pattern
#: rather than something stripped from the request.
console_patterns = [
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
    # The language switch posts here. The console has its own default, but an
    # operator's choice is theirs — see tenancy.middleware.LanguageMiddleware.
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("apps.console.urls")),
]

#: Host mode: the console owns the whole hostname, so its routes sit at the
#: root. `cms.urls_console_path` mounts the same list under a prefix for the
#: single-host case; the middleware picks whichever the request arrived by, so
#: a deployment with both keeps working on both.
urlpatterns = list(console_patterns)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
