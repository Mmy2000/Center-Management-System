from django.urls import path

from . import auth_api, views

app_name = "accounts"

urlpatterns = [
    path("login/", views.CenterLoginView.as_view(), name="login"),
    path("login/ajax/", auth_api.login_ajax, name="login_ajax"),
    path("logout/", views.CenterLogoutView.as_view(), name="logout"),
    path(
        "password/change/",
        views.CenterPasswordChangeView.as_view(),
        name="password_change",
    ),
    path("profile/", views.profile, name="profile"),
    path("users/", views.users_page, name="users"),
]
