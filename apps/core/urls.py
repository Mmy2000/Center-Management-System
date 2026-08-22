from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("healthz/", views.healthz, name="healthz"),
    path("readyz/", views.readyz, name="readyz"),
    path("settings/", views.settings_page, name="settings"),
    path("audit/", views.audit_log_page, name="audit_log"),
]
