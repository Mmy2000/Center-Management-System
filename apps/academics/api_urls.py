from django.urls import path

from . import api

app_name = "academics_api"

urlpatterns = [
    path("stages/", api.stages, name="stages"),
    path("stages/<int:pk>/", api.stage_detail, name="stage_detail"),
    path("grades/", api.grades, name="grades"),
    path("grades/<int:pk>/", api.grade_detail, name="grade_detail"),
    path("subjects/", api.subjects, name="subjects"),
    path("subjects/<int:pk>/", api.subject_detail, name="subject_detail"),
    path("offerings/", api.offerings, name="offerings"),
    path("offerings/<int:pk>/", api.offering_detail, name="offering_detail"),
    path("instructors/", api.instructors, name="instructors"),
    path("instructors/<int:pk>/", api.instructor_detail, name="instructor_detail"),
    path("groups/", api.groups, name="groups"),
    path("groups/<int:pk>/", api.group_detail, name="group_detail"),
    path("groups/<int:pk>/schedule/", api.group_schedules, name="group_schedules"),
    path("schedule/<int:pk>/", api.schedule_detail, name="schedule_detail"),
]
