from django.shortcuts import render
from django.utils import timezone

from apps.academics.models import Grade, Subject
from apps.accounts.decorators import require_feature, require_perm

from .models import ChargeStatus, PaymentMethod


@require_perm("payments.view_monthlycharge")
@require_feature("payments")
def workspace(request):
    """Charges + day book, one screen (docs/05 §H.3)."""
    today = timezone.localdate()
    months = []
    year, month = today.year, today.month
    for _ in range(12):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1

    return render(
        request,
        "payments/workspace.html",
        {
            "months": months,
            "current_month": f"{today.year:04d}-{today.month:02d}",
            "today": today.isoformat(),
            "statuses": ChargeStatus.choices,
            "methods": PaymentMethod.choices,
            "subjects": Subject.objects.filter(is_active=True),
            "grades": Grade.objects.filter(is_active=True).select_related("stage"),
        },
    )
