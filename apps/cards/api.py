"""Card endpoints (TASK-030 → 034).

The lookup endpoint is what the enrollment scan calls: it answers "what is this
card?" and never leaks the holder's identity to a role that may not see
students.
"""

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from apps.accounts.decorators import require_feature, require_perm
from apps.core import ratelimit
from apps.core.http import DomainError, ajax
from apps.students.models import Student

from . import documents, importer, services
from .models import CardStatus, StudentCard

PAGE_SIZE = 50


def card_json(card: StudentCard, *, can_see_students=False) -> dict:
    data = {
        "id": card.pk,
        "card_number": card.card_number,
        "token_masked": card.masked_token,
        "status": card.status,
        "status_display": card.get_status_display(),
        "batch": card.batch,
        "issued_at": card.issued_at.isoformat() if card.issued_at else None,
    }
    if can_see_students and card.current_student_id:
        data["student"] = {
            "id": card.current_student_id,
            "name": card.current_student.full_name,
            "code": card.current_student.student_code,
            "grade": str(card.current_student.grade),
        }
    else:
        data["student"] = {"id": card.current_student_id} if card.current_student_id else None
    return data


@ajax(methods=["GET"], perm="cards.view_studentcard", feature="cards")
def cards(request):
    qs = StudentCard.objects.select_related("current_student__grade")
    params = request.GET
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("batch"):
        qs = qs.filter(batch=params["batch"])
    if params.get("q"):
        qs = qs.filter(card_number__icontains=params["q"])

    paginator = Paginator(qs, PAGE_SIZE)
    page = paginator.get_page(params.get("page") or 1)
    can_see = request.user.has_perm("students.view_student")
    return {
        "results": [card_json(card, can_see_students=can_see) for card in page.object_list],
        "page": page.number,
        "pages": paginator.num_pages,
        "count": paginator.count,
        "has_next": page.has_next(),
        "has_previous": page.has_previous(),
        "totals": {
            status: StudentCard.objects.filter(status=status).count()
            for status, _label in CardStatus.choices
        },
    }


@ajax(methods=["POST"], perm="cards.view_studentcard", feature="cards")
def lookup(request):
    """Enrollment scan: resolve a scanned token to a card and its state."""
    ratelimit.check("card_lookup", str(request.user.pk), limit=30, window=60)
    card = services.find_card(request.json.get("qr_token", ""))
    can_see = request.user.has_perm("students.view_student")

    payload = card_json(card, can_see_students=can_see)
    payload["assignable"] = card.status == CardStatus.AVAILABLE
    if card.status == CardStatus.AVAILABLE:
        return {"card": payload, "code": "OK_CARD_AVAILABLE"}

    code, message = (
        ("ERR_CARD_ALREADY_ASSIGNED", _("البطاقة مخصصة لطالب آخر"))
        if card.status == CardStatus.ASSIGNED
        else services.STATUS_ERRORS[card.status]
    )
    raise DomainError(code, message, status=409, data={"card": payload})


@ajax(methods=["POST"], perm="cards.change_studentcard", feature="cards")
def assign(request):
    data = request.json
    if data.get("qr_token"):
        card = services.find_card(data["qr_token"])
    else:
        card = get_object_or_404(StudentCard, pk=data.get("card_id"))
    student = get_object_or_404(Student, pk=data.get("student_id"))

    card = services.assign_card(card, student, actor=request.user, notes=data.get("notes", ""))
    return {"card": card_json(card, can_see_students=True), "code": "OK_CARD_ASSIGNED"}


@ajax(methods=["POST"], perm="cards.change_studentcard", feature="cards")
def mark_lost(request, pk):
    card = get_object_or_404(StudentCard, pk=pk)
    card = services.mark_lost(card, actor=request.user, reason=request.json.get("reason", ""))
    return {"card": card_json(card)}


@ajax(methods=["POST"], perm="cards.change_studentcard", feature="cards")
def disable(request, pk):
    card = get_object_or_404(StudentCard, pk=pk)
    card = services.disable_card(card, actor=request.user, reason=request.json.get("reason", ""))
    return {"card": card_json(card)}


@ajax(methods=["DELETE"], perm="cards.delete_studentcard", feature="cards")
def delete_card(request, pk):
    """Remove unused stock. A card with any history is refused, not deleted."""
    card = get_object_or_404(StudentCard.objects.all(), pk=pk)
    result = services.delete_card(
        card, actor=request.user, reason=(request.json.get("reason") or "").strip()
    )
    return {**result, "code": "OK_CARD_DELETED"}


@ajax(methods=["POST"], perm="cards.change_studentcard", feature="cards")
def replace(request, pk):
    old_card = get_object_or_404(StudentCard, pk=pk)
    data = request.json
    if data.get("new_qr_token"):
        new_card = services.find_card(data["new_qr_token"])
    else:
        new_card = get_object_or_404(StudentCard, pk=data.get("new_card_id"))

    old_card, new_card = services.replace_card(
        old_card,
        new_card,
        actor=request.user,
        reason=data.get("reason", ""),
        old_status=data.get("old_status", CardStatus.REPLACED),
    )
    return {
        "old_card": card_json(old_card),
        "new_card": card_json(new_card, can_see_students=True),
        "code": "OK_CARD_REPLACED",
    }


@ajax(methods=["GET"], perm="cards.view_cardassignment", feature="cards")
def card_history(request, pk):
    card = get_object_or_404(StudentCard, pk=pk)
    return {
        "results": [_history_json(row) for row in card.assignment_history.select_related("student")]
    }


@ajax(methods=["GET"], perm="cards.view_cardassignment", feature="cards")
def student_cards(request, pk):
    """Card timeline on the student profile (§18)."""
    student = get_object_or_404(Student, pk=pk)
    rows = student.card_history.select_related("card", "assigned_by", "released_by")
    return {
        "results": [_history_json(row, with_card=True) for row in rows],
        "active_card": next(
            (
                card_json(card)
                for card in StudentCard.objects.filter(
                    current_student=student, status=CardStatus.ASSIGNED
                )
            ),
            None,
        ),
    }


def _history_json(row, *, with_card=False) -> dict:
    data = {
        "id": row.pk,
        "assigned_at": row.assigned_at.isoformat(),
        "released_at": row.released_at.isoformat() if row.released_at else None,
        "release_reason": row.release_reason,
        "release_reason_display": row.get_release_reason_display() if row.release_reason else "",
        "is_open": row.is_open,
        "notes": row.notes,
    }
    if with_card:
        data["card"] = {
            "id": row.card_id,
            "card_number": row.card.card_number,
            "status": row.card.status,
            "status_display": row.card.get_status_display(),
        }
    else:
        data["student"] = {"id": row.student_id, "name": row.student.full_name}
    return data


# --------------------------------------------------------------------------- #
# Stock creation (TASK-029)
# --------------------------------------------------------------------------- #

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


@ajax(methods=["POST"], perm="cards.add_studentcard", feature="cards.bulk_import")
def generate(request):
    """Mint new blank cards, then export the sheet for the print shop."""
    data = request.json
    try:
        count = int(data.get("count", 0))
    except (TypeError, ValueError) as exc:
        raise DomainError(
            "ERR_VALIDATION",
            _("عدد غير صحيح"),
            field_errors={"count": [_("عدد غير صحيح")]},
        ) from exc

    result = importer.generate_batch(
        count, batch=(data.get("batch") or "").strip()[:30], actor=request.user
    )
    return {**result, "code": "OK_CARDS_GENERATED"}


@ajax(methods=["POST"], perm="cards.add_studentcard", feature="cards.bulk_import")
def import_csv(request):
    """Upload a vendor's CSV. All-or-nothing: one bad row aborts the batch."""
    upload = request.FILES.get("file")
    if upload is None:
        raise DomainError(
            "ERR_VALIDATION",
            _("اختر ملف CSV"),
            field_errors={"file": [_("الملف مطلوب")]},
        )
    if upload.size > MAX_UPLOAD_BYTES:
        raise DomainError(
            "ERR_FILE_TOO_LARGE",
            _("حجم الملف أكبر من 2 ميجابايت"),
            status=400,
            field_errors={"file": [_("ملف كبير جدًا")]},
        )

    try:
        # utf-8-sig strips the BOM Excel adds.
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DomainError(
            "ERR_ENCODING",
            _("الملف ليس بترميز UTF-8 — احفظه من Excel كـ CSV UTF-8"),
            field_errors={"file": [_("ترميز غير مدعوم")]},
        ) from exc

    result = importer.import_batch(
        text, batch=(request.POST.get("batch") or "").strip()[:30], actor=request.user
    )
    return {**result, "code": "OK_CARDS_IMPORTED"}


@require_perm("cards.view_studentcard")
@require_feature("cards.bulk_import")
def export_batch(request):
    """The print shop's copy: a QR sheet (PDF) or the raw token list (CSV).

    The CSV contains live tokens, so it is available to card managers only and
    the export is audited like any other.
    """
    import csv as csv_module
    import io as io_module

    from django.http import HttpResponse

    from apps.core.audit import record
    from apps.core.models import AuditAction

    batch = (request.GET.get("batch") or "").strip()
    fmt = (request.GET.get("format") or "pdf").lower()
    cards = StudentCard.objects.all()
    if batch:
        cards = cards.filter(batch=batch)
    if request.GET.get("status"):
        cards = cards.filter(status=request.GET["status"])
    cards = list(cards.order_by("card_number")[:2000])

    record(
        AuditAction.CARD_IMPORTED,
        changes={"exported": len(cards), "batch": batch, "format": fmt},
        reason=_("تصدير كشف بطاقات"),
        object_repr=f"export:{batch or 'all'}",
        actor=request.user,
    )

    filename = f"cards-{batch or 'all'}"
    if fmt == "csv":
        if not request.user.has_perm("cards.change_studentcard"):
            raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية تصدير الرموز"), status=403)
        buffer = io_module.StringIO()
        writer = csv_module.writer(buffer)
        writer.writerow(["card_number", "qr_token", "batch", "status"])
        for card in cards:
            writer.writerow([card.card_number, card.qr_token, card.batch, card.status])
        response = HttpResponse("﻿" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
        return response

    document = documents.build_card_sheet(cards, request=request, batch=batch)
    return document.response(filename)


# --------------------------------------------------------------------------- #
# Issuing a card to one student (the second path on the student profile)
# --------------------------------------------------------------------------- #


@ajax(methods=["GET"], perm="cards.view_studentcard", feature="cards")
def available(request):
    """Stock the front desk can hand out right now."""
    qs = StudentCard.objects.available()
    if request.GET.get("batch"):
        qs = qs.filter(batch=request.GET["batch"])

    rows = list(qs.order_by("card_number")[:50])
    batches = list(
        StudentCard.objects.available()
        .exclude(batch="")
        .values_list("batch", flat=True)
        .distinct()
        .order_by("batch")
    )
    return {
        "results": [
            {"id": card.pk, "card_number": card.card_number, "batch": card.batch} for card in rows
        ],
        "count": qs.count(),
        "batches": batches,
    }


@ajax(methods=["POST"], perm="cards.change_studentcard", feature="cards")
def issue(request):
    """Give this student a card without scanning anything.

    Takes the chosen card, or the next available one, or — when stock is empty
    and the operator asks for it — mints a fresh one. Then the UI downloads the
    printable face.
    """
    data = request.json
    student = get_object_or_404(Student, pk=data.get("student_id"))

    if data.get("card_id"):
        card = get_object_or_404(StudentCard, pk=data["card_id"])
    else:
        card = StudentCard.objects.available().order_by("card_number").first()
        if card is None:
            if not data.get("mint"):
                raise DomainError(
                    "ERR_NO_STOCK",
                    _("لا توجد بطاقات متاحة — أنشئ دفعة جديدة أولًا"),
                    status=409,
                )
            if not request.user.has_perm("cards.add_studentcard"):
                raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية إنشاء بطاقات"), status=403)
            importer.generate_batch(1, batch=data.get("batch", ""), actor=request.user)
            card = StudentCard.objects.available().order_by("-id").first()

    card = services.assign_card(card, student, actor=request.user, notes=_("إصدار من المخزون"))
    return {
        "card": card_json(card, can_see_students=True),
        "print_url": f"/api/cards/{card.pk}/print/",
        "code": "OK_CARD_ISSUED",
    }


@require_perm("cards.view_studentcard")
@require_feature("cards")
def print_card(request, pk):
    """The card face, ready for a card printer."""
    card = get_object_or_404(StudentCard.objects.select_related("current_student__grade"), pk=pk)
    document = documents.build_student_card(card, request=request)
    return document.response(documents.card_filename(card))
