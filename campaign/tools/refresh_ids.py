"""Pick the records the capture run should photograph.

Always the fullest example of each thing: an empty table is a bad advert for a
system whose whole pitch is that it holds a center's real workload.
"""

import json
import os
import sys

import django

sys.path.insert(0, r"D:\projects\django\Center_Management_System")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms.settings.dev")
django.setup()

from django.db.models import Count  # noqa: E402

from apps.academics.models import Group  # noqa: E402
from apps.attendance.models import Attendance  # noqa: E402
from apps.cards.models import StudentCard  # noqa: E402
from apps.lessons.models import Lesson, LessonStatus  # noqa: E402
from apps.students.models import (  # noqa: E402
    AssignmentStatus,
    Student,
    StudentGroupAssignment,
)
from apps.tenancy.context import tenant_context  # noqa: E402
from apps.tenancy.models import Tenant  # noqa: E402


def main():
    with tenant_context(Tenant.objects.get(slug="demo")):
        hero = (
            Lesson.objects.filter(status=LessonStatus.OPEN)
            .annotate(n=Count("attendances"))
            .order_by("-n")
            .first()
        )
        past = (
            Lesson.objects.filter(status=LessonStatus.COMPLETED)
            .annotate(n=Count("attendances"))
            .order_by("-n")
            .first()
        )
        group = Group.objects.annotate(n=Count("assignments")).order_by("-n").first()
        student = (
            Student.objects.annotate(n=Count("assignments")).order_by("-n").first()
        )

        checked_in = set(
            Attendance.objects.filter(lesson=hero).values_list("student_id", flat=True)
        )
        pending = StudentGroupAssignment.objects.filter(
            group=hero.group, status=AssignmentStatus.ACTIVE
        ).exclude(student_id__in=checked_in).values_list("student_id", flat=True)
        tokens = list(
            StudentCard.objects.filter(
                current_student_id__in=list(pending)
            ).values_list("qr_token", flat=True)[:4]
        )

        ids = {
            "lesson": hero.pk,
            "pastLesson": past.pk,
            "group": group.pk,
            "student": student.pk,
            "heroTokens": tokens,
            "heroToken": tokens[0] if tokens else "",
        }
        path = os.path.join(os.environ["SCRATCH"], "ids.json")
        with open(path, "w") as fh:
            json.dump(ids, fh)
        print(f"  ids: {ids['lesson']=} {ids['pastLesson']=} {ids['group']=} "
              f"{ids['student']=} tokens={len(tokens)}")
        if len(tokens) < 2:
            print("  WARNING: fewer than two spare cards - the second language "
                  "run will photograph a duplicate-scan verdict")


if __name__ == "__main__":
    main()
