"""Console routes — mounted only from ``cms/urls_console.py`` (TASK-111).

Deliberately never included from ``cms/urls.py``. The two URLconfs are disjoint,
which is what makes a tenant host structurally unable to reach a console view.
"""

from django.urls import path

from . import api, views

app_name = "console"

LOGIN_URL = "/login/"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.console_login, name="login"),
    path("logout/", views.console_logout, name="logout"),
    path("tenants/", views.tenant_list, name="tenant_list"),
    path("tenants/new/", views.tenant_new, name="tenant_new"),
    path("tenants/<int:pk>/", views.tenant_detail, name="tenant_detail"),
    path("tenants/<int:pk>/features/", views.tenant_features, name="tenant_features"),
    path("tenants/<int:pk>/features/set/", views.tenant_feature_set, name="tenant_feature_set"),
    path("tenants/<int:pk>/status/", views.tenant_status, name="tenant_status"),
    path("tenants/<int:pk>/plan/", views.tenant_plan, name="tenant_plan"),
    path("tenants/<int:pk>/usage/", views.tenant_usage_refresh, name="tenant_usage_refresh"),
    path("tenants/<int:pk>/enter/", views.tenant_enter, name="tenant_enter"),
    path("tenants/<int:pk>/export/", views.tenant_export, name="tenant_export"),
    path("tenants/<int:pk>/delete/", views.tenant_delete, name="tenant_delete"),
    path("leave/", views.tenant_leave, name="tenant_leave"),
    path("plans/", views.plan_list, name="plan_list"),
    # JSON, through the same envelope and the same http.js wrapper the product
    # uses — so the loading bar, the toasts and the error handling are the ones
    # already built (docs/05 §G.2).
    path("api/tenants/", api.tenants, name="api_tenants"),
    path("api/overview/", api.overview, name="api_overview"),
    path("api/slug-check/", api.slug_check, name="api_slug_check"),
    path("api/tenants/<int:pk>/status/", api.tenant_status, name="api_tenant_status"),
    path("api/tenants/<int:pk>/usage/", api.tenant_usage, name="api_tenant_usage"),
    path("api/tenants/<int:pk>/plan/", api.tenant_plan, name="api_tenant_plan"),
    path("api/tenants/<int:pk>/feature/", api.tenant_feature, name="api_tenant_feature"),
    path("audit/", views.audit, name="audit"),
    path("health/", views.health, name="health"),
]
