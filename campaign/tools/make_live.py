"""Put a few of today's lessons *in session* around the wall clock.

The scan screen is the product's centrepiece and only looks like itself when a
lesson is genuinely open, with a roster inside the room and a scan feed
ticking. Lesson windows are frozen at creation, so this has to be re-run
whenever the capture happens at a different hour.

The check-in window closes 150 minutes after the scheduled start, so a lesson
can be backdated far enough to read as a normal evening session and still be
open right now.
"""

import os
import random
import sys
from datetime import timedelta

import django

sys.path.insert(0, r"D:\projects\django\Center_Management_System")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms.settings.dev")
django.setup()

from django.db.models import Count, Q  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import User  # noqa: E402
from apps.attendance.models import (  # noqa: E402
    Attendance,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceState,
    AttendanceStatus,
    AttendanceType,
    ScanSource,
)
from apps.cards.models import StudentCard  # noqa: E402
from apps.lessons.models import Lesson, LessonStatus  # noqa: E402
from apps.lessons.services import invalidate_lesson_cache, resolve_windows  # noqa: E402
from apps.students.models import AssignmentStatus, StudentGroupAssignment  # noqa: E402
from apps.tenancy.context import tenant_context  # noqa: E402
from apps.tenancy.models import Tenant  # noqa: E402

random.seed(7)

DEVICES = ["desk-01", "desk-02", "gate-01"]


def main():
    tenant = Tenant.objects.get(slug="demo")
    with tenant_context(tenant):
        run()


def run():
    now = timezone.now()
    today = timezone.localdate()
    operator = User.objects.filter(username="scan").first()

    candidates = (
        Lesson.objects.filter(lesson_date=today)
        .annotate(n=Count("attendances"))
        .select_related("group")
        .order_by("-n")[:4]
    )

    # Earlier runs left lessons OPEN whose end time has since passed, so the
    # dashboard grew a "running now" list full of sessions that had finished.
    # Close everything first; only the four chosen below stay open.
    Lesson.objects.filter(status=LessonStatus.OPEN).update(
        status=LessonStatus.COMPLETED
    )

    live = []
    candidates = list(candidates)
    for index, lesson in enumerate(candidates):
        # The first lesson is the one the capture run scans into, and it is
        # about to start: check-in opens 30 minutes early, so the room fills
        # while the clock runs down. Scanning before the start is the only way
        # the verdict reads as genuinely on time - late_minutes counts from
        # scheduled_start, not from the lateness threshold.
        # The rest are backdated far enough to read as evening sessions and
        # close enough that the 150-minute window is still open.
        start = (
            now + timedelta(minutes=14)
            if index == 0
            else now - timedelta(minutes=95 + index * 7)
        )
        end = start + timedelta(hours=2)
        opens, closes, late_after = resolve_windows(lesson.group, start)
        lesson.scheduled_start = start
        lesson.scheduled_end = end
        lesson.lesson_date = timezone.localtime(start).date()
        lesson.check_in_opens_at = opens
        lesson.check_in_closes_at = closes
        lesson.late_after = late_after
        lesson.status = LessonStatus.OPEN
        lesson.actual_start_at = start
        lesson.actual_end_at = None
        lesson.expected_students = StudentGroupAssignment.objects.filter(
            group=lesson.group,
            status=AssignmentStatus.ACTIVE,
            start_date__lte=lesson.lesson_date,
        ).count()
        lesson.save()
        invalidate_lesson_cache(lesson.pk)
        live.append(lesson)

    # A center that has issued every card it owns has none left to issue. Keep
    # blank stock on the shelf so the card screen shows an inventory.
    from apps.cards.importer import generate_batch
    from apps.cards.models import CardStatus, StudentCard

    spare = StudentCard.objects.filter(status=CardStatus.AVAILABLE).count()
    if spare < 60:
        generate_batch(60 - spare, batch="BATCH-2026-B")
        print(f"  blank cards topped up to 60 (added {60 - spare})")

    # Right-size the rooms. Capacity is advisory in this product, but a roster
    # of 38 in a 30-seat group photographs as a data error rather than as a
    # popular class.
    from apps.academics.models import Group

    resized = 0
    for group in Group.objects.annotate(
        roster=Count(
            "assignments",
            filter=Q(assignments__status=AssignmentStatus.ACTIVE),
        )
    ):
        target = max(30, ((group.roster + 7) // 5) * 5)
        if group.capacity != target:
            group.capacity = target
            group.save(update_fields=["capacity"])
            resized += 1
    print(f"  capacity right-sized on {resized} groups")

    backfilled = 0
    for lesson in Lesson.objects.exclude(pk__in=[l.pk for l in live]).select_related("group"):
        size = StudentGroupAssignment.objects.filter(
            group_id=lesson.group_id,
            status=AssignmentStatus.ACTIVE,
            start_date__lte=lesson.lesson_date,
        ).count()
        if lesson.expected_students != size:
            lesson.expected_students = size
            lesson.save(update_fields=["expected_students"])
            backfilled += 1
    print(f"  expected_students backfilled on {backfilled} lessons")

    AttendanceEvent.objects.filter(lesson__in=live).delete()

    for lesson in live:
        # Rebuild from the roster rather than editing whatever rows survive, so
        # re-running this does not shave the class down a little each time.
        Attendance.objects.filter(lesson=lesson).delete()
        roster = list(
            StudentGroupAssignment.objects.filter(
                group=lesson.group,
                status=AssignmentStatus.ACTIVE,
                start_date__lte=lesson.lesson_date,
            ).select_related("student")
        )
        random.shuffle(roster)
        # Some of the class has not walked in yet: the app represents "not
        # attended" as the absence of a row, so simply do not create one.
        arrived = [
            Attendance.objects.create(
                lesson=lesson,
                student=a.student,
                assigned_group_id=lesson.group_id,
                attended_group_id=lesson.group_id,
                state=AttendanceState.CHECKED_IN,
                status=AttendanceStatus.PRESENT,
                attendance_type=AttendanceType.NORMAL,
                scan_source=ScanSource.HID,
                check_in_at=lesson.scheduled_start,
            )
            for a in roster[: int(len(roster) * 0.82)]
        ]

        cards = {
            c.current_student_id: c
            for c in StudentCard.objects.filter(
                current_student_id__in=[a.student_id for a in arrived]
            )
        }

        events = []
        starting_soon = lesson.scheduled_start > now
        for position, attendance in enumerate(arrived):
            late = (not starting_soon) and position % 11 == 0
            if starting_soon:
                # Arriving over the last half hour - anchored to the clock, not
                # to the start, so no check-in lands in the future.
                check_in = now - timedelta(
                    minutes=2 + (position * 23) % 28, seconds=random.randint(0, 59)
                )
            else:
                check_in = lesson.scheduled_start + timedelta(
                    minutes=(18 if late else 0) + position % 9,
                    seconds=random.randint(0, 59),
                )
            attendance.state = AttendanceState.CHECKED_IN
            attendance.status = (
                AttendanceStatus.LATE if late else AttendanceStatus.PRESENT
            )
            attendance.check_in_at = check_in
            attendance.check_out_at = None
            attendance.late_minutes = 18 if late else 0
            attendance.status = (
                AttendanceStatus.LATE if late else AttendanceStatus.PRESENT
            )
            attendance.duration_minutes = None
            attendance.scan_source = ScanSource.HID
            attendance.checked_in_by = operator
            attendance.save()

            card = cards.get(attendance.student_id)
            events.append(
                (
                    AttendanceEvent(
                        attendance=attendance,
                        lesson=lesson,
                        student=attendance.student,
                        card=card,
                        event_type=AttendanceEventType.CHECK_IN,
                        result_code="OK_LATE" if late else "OK",
                        message="تسجيل حضور" if not late else "تسجيل حضور متأخر",
                        device_id=random.choice(DEVICES),
                        operator=operator,
                        latency_ms=random.randint(38, 140),
                    ),
                    check_in,
                )
            )

        AttendanceEvent.objects.bulk_create([e for e, _ in events])
        # created_at is auto_now_add, so the realistic timeline has to be
        # written back after the insert.
        for event, when in events:
            AttendanceEvent.objects.filter(pk=event.pk).update(created_at=when)

    for lesson in live:
        inside = Attendance.objects.filter(
            lesson=lesson, state=AttendanceState.CHECKED_IN
        ).count()
        print(
            f"  live: {lesson.pk} {lesson.group.code} "
            f"{timezone.localtime(lesson.scheduled_start):%H:%M}"
            f"-{timezone.localtime(lesson.scheduled_end):%H:%M} "
            f"inside={inside}/{lesson.expected_students} "
            f"events={AttendanceEvent.objects.filter(lesson=lesson).count()}"
        )

    # A card belonging to someone on the busiest live lesson's roster who has
    # NOT checked in yet - the capture run scans it for the hero shot.
    hero = live[0]
    present = set(
        Attendance.objects.filter(lesson=hero).values_list("student_id", flat=True)
    )
    pending = (
        StudentGroupAssignment.objects.filter(
            group=hero.group, status=AssignmentStatus.ACTIVE
        )
        .exclude(student_id__in=present)
        .values_list("student_id", flat=True)
    )
    card = StudentCard.objects.filter(current_student_id__in=list(pending)).first()
    print(f"HERO_LESSON={hero.pk}")
    print(f"HERO_TOKEN={card.qr_token if card else ''}")


if __name__ == "__main__":
    main()
