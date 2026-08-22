from django.shortcuts import render

from apps.accounts.decorators import require_perm

from .models import CardStatus, StudentCard


@require_perm("cards.view_studentcard")
def cards_page(request):
    batches = (
        StudentCard.objects.exclude(batch="")
        .values_list("batch", flat=True)
        .distinct()
        .order_by("batch")
    )
    return render(
        request,
        "cards/list.html",
        {"statuses": CardStatus.choices, "batches": list(batches)},
    )
