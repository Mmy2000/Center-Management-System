from django.shortcuts import get_object_or_404, render

from apps.academics.models import EducationalStage, Grade
from apps.accounts.decorators import require_perm
from apps.accounts.scoping import visible_students

from .models import Student, StudentStatus


@require_perm("students.view_student")
def students_page(request):
    return render(
        request,
        "students/list.html",
        {
            "stages": EducationalStage.objects.filter(is_active=True),
            "grades": Grade.objects.filter(is_active=True).select_related("stage"),
            "statuses": StudentStatus.choices,
        },
    )


@require_perm("students.add_student")
def student_create_page(request):
    return render(
        request,
        "students/create.html",
        {
            "grades": Grade.objects.filter(is_active=True).select_related("stage"),
            "statuses": StudentStatus.choices,
        },
    )


@require_perm("students.change_student")
def student_edit_page(request, pk):
    """Edit a student's own details.

    The saving is the PATCH endpoint that already existed and is already
    audited; this is the screen that was missing. Status changes stay on the
    detail page — they carry their own reason and their own audit action, and
    folding them in here would let a status change ride along unnoticed with a
    phone-number correction.
    """
    student = get_object_or_404(
        visible_students(request.user, Student.objects.with_related()), pk=pk
    )
    return render(
        request,
        "students/edit.html",
        {
            "student": student,
            "grades": Grade.objects.filter(is_active=True).select_related("stage"),
        },
    )


@require_perm("students.view_student")
def student_detail_page(request, pk):
    student = get_object_or_404(
        visible_students(request.user, Student.objects.with_related()), pk=pk
    )
    return render(request, "students/detail.html", {"student": student})
