from django.urls import path

from . import api

app_name = "core_api"

urlpatterns = [
    path("settings/", api.settings_list, name="settings_list"),
    path("settings/update/", api.settings_update, name="settings_update"),
]
