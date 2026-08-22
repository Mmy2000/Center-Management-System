from django.urls import path

from . import views

app_name = "tenancy"

urlpatterns = [
    path("suspended/", views.gate, name="gate"),
    path("internal/tls-check/", views.tls_check, name="tls_check"),
]
