from django.urls import path

from . import api, assignment_api

app_name = "students_api"

urlpatterns = [
    path("students/", api.students, name="students"),
    path("students/search/", api.student_search, name="student_search"),
    path("students/statuses/", api.student_statuses, name="student_statuses"),
    path("students/<int:pk>/", api.student_detail, name="student_detail"),
    path("students/<int:pk>/status/", api.student_status, name="student_status"),
    path(
        "students/<int:pk>/assignments/",
        assignment_api.student_assignments,
        name="student_assignments",
    ),
    path("groups/<int:pk>/students/", assignment_api.group_students, name="group_students"),
    path(
        "groups/<int:pk>/students/<int:student_id>/",
        assignment_api.group_student_detail,
        name="group_student_detail",
    ),
    path("assignments/<int:pk>/transfer/", assignment_api.transfer, name="transfer"),
]
