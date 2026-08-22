from django.urls import path

from . import api

app_name = "payments_api"

urlpatterns = [
    path("charges/", api.charges, name="charges"),
    path("charges/generate/", api.generate, name="generate"),
    path("charges/<int:pk>/", api.charge_detail, name="charge_detail"),
    path("charges/<int:pk>/waive/", api.waive, name="waive"),
    path("charges/<int:pk>/cancel/", api.cancel, name="cancel"),
    path("payments/", api.payments, name="payments"),
    path("payments/<int:pk>/refund/", api.refund, name="refund"),
    path("payments/<int:pk>/receipt/", api.receipt, name="receipt"),
    path("students/<int:pk>/payments/", api.student_payments, name="student_payments"),
    path(
        "students/<int:pk>/financial-summary/",
        api.student_financial_summary,
        name="student_financial_summary",
    ),
]
