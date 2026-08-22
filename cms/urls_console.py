"""URLconf for the platform console host (docs/10 §N.10).

Selected by `TenantResolutionMiddleware` when the request arrives on
`settings.CONSOLE_HOST`. It is a *separate* URLconf, not a prefix inside
`cms.urls`, so a tenant host physically cannot route to a console view — no
permission bug, no path-traversal trick and no proxy misconfiguration can
bridge the two.

TASK-111 mounts the console app here. Until then this carries only the health
checks, so the console host is reachable and monitorable.
"""

from django.urls import include, path

urlpatterns = [
    path("", include("apps.core.urls")),
]
