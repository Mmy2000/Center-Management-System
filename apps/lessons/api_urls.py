from django.urls import path

from . import api

app_name = "lessons_api"

urlpatterns = [
    path("lessons/", api.lessons, name="lessons"),
    path("lessons/active/", api.active_lessons, name="active_lessons"),
    path("lessons/generate/", api.generate, name="generate"),
    path("lessons/<int:pk>/", api.lesson_detail, name="lesson_detail"),
    path("lessons/<int:pk>/open/", api.open_lesson, name="open_lesson"),
    path("lessons/<int:pk>/complete/", api.complete_lesson, name="complete_lesson"),
    path("lessons/<int:pk>/cancel/", api.cancel_lesson, name="cancel_lesson"),
]
