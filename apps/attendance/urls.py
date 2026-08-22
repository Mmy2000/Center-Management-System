from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    path("scan/", views.scanner_picker, name="scanner_picker"),
    path("scan/<int:pk>/", views.scanner_console, name="scanner"),
]
