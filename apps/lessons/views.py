from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _

from apps.accounts.decorators import require_perm
from apps.accounts.scoping import visible_groups, visible_lessons

from .models import Lesson, LessonStatus


@require_perm("lessons.view_lesson")
def lessons_page(request):
    return render(
        request,
        "lessons/list.html",
        {
            "groups": visible_groups(request.user).select_related(
                "grade_subject__subject", "grade_subject__grade"
            ),
            "statuses": LessonStatus.choices,
        },
    )


@require_perm("lessons.view_lesson")
def lesson_detail_page(request, pk):
    lesson = get_object_or_404(visible_lessons(request.user, Lesson.objects.with_related()), pk=pk)
    counters_labels = [
        ("expected", _("متوقع")),
        ("present", _("حاضر")),
        ("late", _("متأخر")),
        ("absent", _("غائب")),
        ("alternative", _("بديل")),
        ("inside", _("بالداخل")),
    ]
    return render(
        request,
        "lessons/detail.html",
        {"lesson": lesson, "counters_labels": counters_labels},
    )
