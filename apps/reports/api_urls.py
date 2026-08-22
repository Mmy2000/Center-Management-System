from django.urls import path

from . import api

app_name = "reports_api"

urlpatterns = [
    path("reports/", api.report_list, name="report_list"),
    path("reports/<slug:slug>/", api.report_data, name="report_data"),
    path("reports/<slug:slug>/export/", api.export, name="export"),
    path("dashboard/summary/", api.dashboard, name="dashboard"),
    path("groups/<int:pk>/stats/", api.group_stats, name="group_stats"),
]
