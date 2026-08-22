from django.urls import path

from . import api

app_name = "accounts_api"

urlpatterns = [
    path("users/", api.users, name="users"),
    path("users/<int:pk>/", api.user_detail, name="user_detail"),
]
