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


@require_perm("students.view_student")
def student_detail_page(request, pk):
    student = get_object_or_404(
        visible_students(request.user, Student.objects.with_related()), pk=pk
    )
    return render(request, "students/detail.html", {"student": student})
