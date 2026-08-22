from django.urls import path

from . import views

app_name = "students"

urlpatterns = [
    path("students/", views.students_page, name="list"),
    path("students/new/", views.student_create_page, name="create"),
    path("students/<int:pk>/", views.student_detail_page, name="detail"),
]
