"""Root URL configuration — see docs/05-api-and-ui.md §G.3."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

urlpatterns = [
    path("admin/", admin.site.urls),
    # Language switching keeps the URL stable (no /ar/ prefix): the choice
    # rides in a cookie, so every hard-coded /api/... path still works.
    path("i18n/", include("django.conf.urls.i18n")),
    # domain="django": one catalog for templates, Python and JS. The default
    # ("djangojs") would need a second .po, and extract_messages writes one.
    path(
        "jsi18n/",
        JavaScriptCatalog.as_view(domain="django"),
        name="javascript-catalog",
    ),
    path("", include("apps.core.urls")),
    path("", include("apps.dashboard.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.academics.urls")),
    path("", include("apps.students.urls")),
    path("", include("apps.cards.urls")),
    path("", include("apps.lessons.urls")),
    path("", include("apps.attendance.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.reports.urls")),
    # JSON / AJAX endpoints (docs/05 §G.3)
    path("api/", include("apps.core.api_urls")),
    path("api/", include("apps.accounts.api_urls")),
    path("api/", include("apps.academics.api_urls")),
    path("api/", include("apps.students.api_urls")),
    path("api/", include("apps.cards.api_urls")),
    path("api/", include("apps.lessons.api_urls")),
    path("api/", include("apps.attendance.api_urls")),
    path("api/", include("apps.payments.api_urls")),
    path("api/", include("apps.reports.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
