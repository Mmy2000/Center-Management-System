from django.urls import path

from . import views

app_name = "academics"

urlpatterns = [
    path("academics/", views.structure_page, name="structure"),
    path("groups/", views.groups_page, name="groups"),
    path("groups/<int:pk>/", views.group_detail_page, name="group_detail"),
]
