from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.decorators import require_feature, require_perm
from apps.accounts.scoping import can_touch_lesson, visible_lessons
from apps.core.http import DomainError
from apps.lessons.models import Lesson, LessonStatus


@require_perm("attendance.add_attendance")
@require_feature("attendance.qr")
def scanner_picker(request):
    """Pick an open lesson; auto-enter when there is exactly one."""
    lessons = visible_lessons(request.user, Lesson.objects.with_related())
    open_lessons = list(lessons.filter(status=LessonStatus.OPEN).order_by("scheduled_start"))
    today = list(
        lessons.filter(status=LessonStatus.SCHEDULED, lesson_date=timezone.localdate()).order_by(
            "scheduled_start"
        )
    )
    return render(
        request,
        "attendance/picker.html",
        {"open_lessons": open_lessons, "today_lessons": today},
    )


@require_perm("attendance.add_attendance")
@require_feature("attendance.qr")
def scanner_console(request, pk):
    lesson = get_object_or_404(visible_lessons(request.user, Lesson.objects.with_related()), pk=pk)
    if not can_touch_lesson(request.user, lesson):
        raise DomainError("ERR_FORBIDDEN", "forbidden", status=403)

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
        "attendance/scanner.html",
        {"lesson": lesson, "counters_labels": counters_labels},
    )
