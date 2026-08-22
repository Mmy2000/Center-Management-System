from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("cards/", views.cards_page, name="list"),
]
