from django.urls import path

from . import api

app_name = "reports"

urlpatterns = [
    path("reports/", api.reports_page, name="index"),
    path("reports/<slug:slug>/", api.reports_page, name="report"),
]
