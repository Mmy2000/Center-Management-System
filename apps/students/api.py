"""Student endpoints (TASK-022 → 025)."""

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from apps.accounts.scoping import visible_students
from apps.core.http import DomainError, ajax

from . import services
from .forms import StudentForm
from .models import Student, StudentStatus

PAGE_SIZE = 25


def student_json(student: Student, *, detail=False) -> dict:
    data = {
        "id": student.pk,
        "student_code": student.student_code,
        "full_name": student.full_name,
        "photo": student.photo.url if student.photo else None,
        "grade_id": student.grade_id,
        "grade": str(student.grade),
        "stage": str(student.grade.stage),
        "status": student.status,
        "status_display": student.get_status_display(),
        "guardian_phone": student.guardian_phone,
        "phone": student.phone,
    }
    if detail:
        data.update(
            {
                "date_of_birth": (
                    student.date_of_birth.isoformat() if student.date_of_birth else None
                ),
                "gender": student.gender,
                "guardian_name": student.guardian_name,
                "guardian_relation": student.guardian_relation,
                "address": student.address,
                "school": student.school,
                "enrolled_on": student.enrolled_on.isoformat(),
                "notes": student.notes,
                "created_at": student.created_at.isoformat(),
            }
        )
    return data


def student_queryset(request):
    qs = visible_students(request.user, Student.objects.with_related())
    params = request.GET
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("grade"):
        qs = qs.filter(grade_id=params["grade"])
    if params.get("stage"):
        qs = qs.filter(grade__stage_id=params["stage"])
    if params.get("q"):
        qs = services.search_students(qs, params["q"])
    return qs.order_by("full_name")


@ajax(methods=["GET", "POST"], perm="students.view_student")
def students(request):
    if request.method == "GET":
        page_number = request.GET.get("page") or 1
        paginator = Paginator(student_queryset(request), PAGE_SIZE)
        page = paginator.get_page(page_number)
        return {
            "results": [student_json(s) for s in page.object_list],
            "page": page.number,
            "pages": paginator.num_pages,
            "count": paginator.count,
            "has_next": page.has_next(),
            "has_previous": page.has_previous(),
        }

    if not request.user.has_perm("students.add_student"):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

    form = StudentForm(request.json)
    if not form.is_valid():
        raise DomainError(
            "ERR_VALIDATION",
            _("بيانات غير صحيحة"),
            field_errors={f: [str(e) for e in errors] for f, errors in form.errors.items()},
        )
    student = services.create_student(actor=request.user, **form.cleaned_data)
    return {"student": student_json(student, detail=True)}


@ajax(methods=["GET", "PATCH"], perm="students.view_student")
def student_detail(request, pk):
    student = get_object_or_404(student_queryset(request), pk=pk)
    if request.method == "GET":
        return {"student": student_json(student, detail=True)}

    if not request.user.has_perm("students.change_student"):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

    data = {}
    for field in StudentForm.Meta.fields:
        if field in request.json:
            data[field] = request.json[field]
        else:
            current = getattr(student, field)
            data[field] = current.pk if hasattr(current, "pk") else current

    # Validate against a *detached* copy: a bound ModelForm mutates its
    # instance during _post_clean, which would poison the audit "before"
    # snapshot taken inside update_student().
    form = StudentForm(data, instance=Student.objects.get(pk=student.pk))
    if not form.is_valid():
        raise DomainError(
            "ERR_VALIDATION",
            _("بيانات غير صحيحة"),
            field_errors={f: [str(e) for e in errors] for f, errors in form.errors.items()},
        )
    updated = services.update_student(
        student,
        actor=request.user,
        reason=request.json.get("reason", ""),
        **{k: v for k, v in form.cleaned_data.items() if k != "photo"},
    )
    return {"student": student_json(updated, detail=True)}


@ajax(methods=["POST"], perm="students.change_student")
def student_status(request, pk):
    student = get_object_or_404(student_queryset(request), pk=pk)
    updated = services.change_status(
        student,
        request.json.get("status", ""),
        actor=request.user,
        reason=request.json.get("reason", ""),
    )
    return {"student": student_json(updated)}


@ajax(methods=["GET"], perm="students.view_student")
def student_search(request):
    """Typeahead feeder: code, name or guardian phone, at most 10 rows."""
    term = (request.GET.get("q") or "").strip()
    if len(term) < 2:
        return {"results": []}
    qs = services.search_students(
        visible_students(request.user, Student.objects.with_related()), term
    )[:10]
    return {
        "results": [
            {
                "id": s.pk,
                "student_code": s.student_code,
                "full_name": s.full_name,
                "grade": str(s.grade),
                "status": s.status,
            }
            for s in qs
        ]
    }


@ajax(methods=["GET"], perm="students.view_student")
def student_statuses(request):
    return {"results": [{"value": v, "label": str(label)} for v, label in StudentStatus.choices]}
