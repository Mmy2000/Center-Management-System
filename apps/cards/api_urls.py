from django.urls import path

from . import api

app_name = "cards_api"

urlpatterns = [
    path("cards/", api.cards, name="cards"),
    path("cards/generate/", api.generate, name="generate"),
    path("cards/import/", api.import_csv, name="import_csv"),
    path("cards/export/", api.export_batch, name="export_batch"),
    path("cards/lookup/", api.lookup, name="lookup"),
    path("cards/assign/", api.assign, name="assign"),
    path("cards/available/", api.available, name="available"),
    path("cards/issue/", api.issue, name="issue"),
    path("cards/<int:pk>/print/", api.print_card, name="print_card"),
    path("cards/<int:pk>/mark-lost/", api.mark_lost, name="mark_lost"),
    path("cards/<int:pk>/disable/", api.disable, name="disable"),
    path("cards/<int:pk>/replace/", api.replace, name="replace"),
    path("cards/<int:pk>/delete/", api.delete_card, name="delete"),
    path("cards/<int:pk>/history/", api.card_history, name="card_history"),
    path("students/<int:pk>/cards/", api.student_cards, name="student_cards"),
]
