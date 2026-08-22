from django.urls import path

from . import views

app_name = "lessons"

urlpatterns = [
    path("lessons/", views.lessons_page, name="list"),
    path("lessons/<int:pk>/", views.lesson_detail_page, name="detail"),
]
