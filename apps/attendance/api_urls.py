from django.urls import path

from . import api

app_name = "attendance_api"

urlpatterns = [
    path("attendance/scan/", api.scan, name="scan"),
    path("attendance/manual/", api.manual, name="manual"),
    path("attendance/<int:pk>/checkout/", api.checkout, name="checkout"),
    path("attendance/<int:pk>/", api.attendance_detail, name="attendance_detail"),
    path("attendance/<int:pk>/cancel/", api.cancel, name="cancel"),
    path("attendance/<int:pk>/approve/", api.approve, name="approve"),
    path("lessons/<int:pk>/attendance/", api.lesson_attendance, name="lesson_attendance"),
    path("lessons/<int:pk>/feed/", api.lesson_feed, name="lesson_feed"),
    path("students/<int:pk>/attendance/", api.student_attendance, name="student_attendance"),
]
