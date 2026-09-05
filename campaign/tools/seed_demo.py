"""Build a rich, realistic demo center for marketing screenshots.

Runs against the throwaway DATABASE_URL only; never the developer's db.sqlite3.
"""

import os
import random
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import django

sys.path.insert(0, r"D:\projects\django\Center_Management_System")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms.settings.dev")
django.setup()

from django.db.models import F  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.academics.models import (  # noqa: E402
    Grade,
    Group,
    GroupSchedule,
    Instructor,
    Subject,
    Weekday,
)
from apps.accounts.models import Role, User  # noqa: E402
from apps.accounts.services import sync_user_group  # noqa: E402
from apps.attendance.models import (  # noqa: E402
    Attendance,
    AttendanceState,
    AttendanceStatus,
    AttendanceType,
    ScanSource,
)
from apps.cards.importer import generate_batch  # noqa: E402
from apps.cards.models import StudentCard  # noqa: E402
from apps.cards.services import assign_card  # noqa: E402
from apps.core.registry import settings_registry as center_settings  # noqa: E402
from apps.lessons.models import Lesson, LessonStatus  # noqa: E402
from apps.lessons.services import generate_lessons  # noqa: E402
from apps.payments import services as pay  # noqa: E402
from apps.payments.models import MonthlyCharge  # noqa: E402
from apps.students import assignment_services as assign_svc  # noqa: E402
from apps.students.models import Student, StudentStatus  # noqa: E402
from apps.students.services import create_student  # noqa: E402
from apps.tenancy.context import tenant_context  # noqa: E402
from apps.tenancy.models import Tenant  # noqa: E402

random.seed(20260905)

TODAY = date(2026, 9, 5)

FIRST_M = [
    "أحمد", "محمد", "محمود", "مصطفى", "يوسف", "عمر", "خالد", "كريم", "زياد", "طارق",
    "حسن", "حسين", "علي", "إبراهيم", "عبد الرحمن", "عبد الله", "مازن", "آدم", "سيف",
    "بلال", "أنس", "رامي", "شريف", "هاني", "وليد", "ياسين", "أمير", "فارس", "نادر",
]
FIRST_F = [
    "سارة", "مريم", "نور", "هبة", "أميرة", "فاطمة", "أسماء", "ملك", "جنى", "رنا",
    "دينا", "ريم", "سلمى", "هدى", "ياسمين", "لمياء", "منة", "شهد", "روان", "حبيبة",
    "إسراء", "آية", "نادية", "سمية", "غادة", "لينا", "تسنيم", "مي",
]
FAMILY = [
    "عبد العزيز", "السيد", "حسن", "إبراهيم", "منصور", "الشناوي", "عبد الحميد", "فؤاد",
    "رمضان", "الشربيني", "زكي", "الغريب", "سليمان", "عوض", "الطنطاوي", "بدر",
    "شعبان", "الديب", "قنديل", "الخولي", "مرسي", "عثمان", "صابر", "الحديدي",
    "نصار", "عبد النبي", "الفقي", "سرور", "حجازي", "المليجي",
]
SCHOOLS = [
    "مدرسة النصر الثانوية", "مدرسة الأورمان الثانوية", "مدرسة طه حسين الثانوية",
    "مدرسة الحرية الثانوية", "مدرسة السلام الثانوية", "مدرسة المستقبل الثانوية",
]
RELATIONS = ["FATHER", "MOTHER", "BROTHER", "UNCLE"]


def phone():
    return random.choice(["010", "011", "012", "015"]) + "".join(
        random.choice("0123456789") for _ in range(8)
    )


def say(msg):
    print(f"  {msg}", flush=True)


def main():
    tenant = Tenant.objects.get(slug="demo")
    tenant.name = "سنتر النخبة التعليمي"
    tenant.name_en = "Al-Nokhba Educational Center"
    tenant.owner_email = "info@nokhba-center.example"
    tenant.owner_phone = "0233445566"
    tenant.save()

    with tenant_context(tenant):
        build(tenant)


def build(tenant):
    admin = User.objects.get(username="admin")
    admin.full_name = "أ. مصطفى عبد الحميد"
    admin.save()

    # ------------------------------------------------------------ identity
    for key, value in [
        ("center.name", "سنتر النخبة التعليمي"),
        ("center.phone", "0233445566"),
        ("center.address", "٢٧ شارع الجمهورية، وسط البلد، القاهرة"),
        ("center.receipt_footer", "شكرًا لثقتكم بنا — سنتر النخبة التعليمي"),
        ("ui.mode", "light"),
        ("ui.accent", "#1f6f8b"),
    ]:
        center_settings.set(key, value, actor=admin)
    say("center identity set")

    # --------------------------------------------------------------- staff
    staff = [
        ("teacher", "أ. محمد عبد الرحمن", Role.INSTRUCTOR),
        ("cashier", "أ. هالة منصور", Role.CASHIER),
        ("scan", "أ. أحمد قنديل", Role.SCAN_OPERATOR),
    ]
    for username, full_name, role in staff:
        # create_user is the only path that stamps the tenant (TenantUserManager);
        # a bare create() would leave tenant NULL and the account invisible.
        if User.objects.filter(username=username).exists():
            continue
        User.all_tenants.filter(username=username, tenant__isnull=True).delete()
        user = User.objects.create_user(
            username=username,
            password="demo12345678",
            full_name=full_name,
            role=role,
            must_change_password=False,
        )
        sync_user_group(user)
    say(f"staff users: {User.objects.count()}")

    # --------------------------------------------------- extra instructors
    subjects = {s.code: s for s in Subject.objects.all()}
    extra = [
        ("أ. ياسر الغريب", ["MATH"]),
        ("أ. منى عبد النبي", ["EN"]),
        ("أ. شريف الديب", ["BIO"]),
        ("أ. نهى سرور", ["AR"]),
    ]
    for full_name, codes in extra:
        inst, _ = Instructor.objects.get_or_create(
            full_name=full_name, defaults={"phone": phone()}
        )
        inst.subjects.set([subjects[c] for c in codes])

    instructors = list(Instructor.objects.all())
    say(f"instructors: {len(instructors)}")

    # ----------------------------------------- fill out the group roster
    # The stock demo tree only covers a few offerings; a real center's
    # timetable is fuller than that, and a half-empty timetable photographs
    # as an unfinished product.
    from apps.academics.models import GradeSubject

    grades = {g.code: g for g in Grade.objects.all()}
    wanted = [
        ("SEC1", "PHY", 400), ("SEC1", "MATH", 400), ("SEC1", "EN", 350),
        ("SEC2", "PHY", 450), ("SEC2", "MATH", 450), ("SEC2", "CHEM", 450),
        ("SEC3", "PHY", 500), ("SEC3", "CHEM", 500), ("SEC3", "MATH", 550),
        ("SEC3", "BIO", 500), ("SEC3", "EN", 450),
        ("PREP3", "MATH", 300), ("PREP3", "EN", 280), ("PREP2", "MATH", 280),
    ]
    slots = [
        (Weekday.SATURDAY, time(14, 0)), (Weekday.SATURDAY, time(16, 0)),
        (Weekday.SUNDAY, time(14, 0)), (Weekday.SUNDAY, time(16, 30)),
        (Weekday.MONDAY, time(15, 0)), (Weekday.MONDAY, time(17, 0)),
        (Weekday.TUESDAY, time(14, 30)), (Weekday.TUESDAY, time(16, 30)),
        (Weekday.WEDNESDAY, time(15, 0)), (Weekday.WEDNESDAY, time(17, 0)),
        (Weekday.THURSDAY, time(14, 0)), (Weekday.THURSDAY, time(16, 0)),
    ]
    rooms = ["قاعة ١", "قاعة ٢", "قاعة ٣", "قاعة ٤", "المعمل"]

    slot_i = 0
    for grade_code, subject_code, fee in wanted:
        offering, _ = GradeSubject.objects.get_or_create(
            grade=grades[grade_code],
            subject=subjects[subject_code],
            defaults={"default_monthly_fee": Decimal(fee)},
        )
        teacher = next(
            (i for i in instructors if subjects[subject_code] in i.subjects.all()),
            instructors[0],
        )
        for letter in ["A", "B"]:
            group, created = Group.objects.get_or_create(
                code=f"{grade_code}-{subject_code}-{letter}",
                defaults={
                    "grade_subject": offering,
                    "name": f"Group {letter}",
                    "name_ar": f"مجموعة {letter}",
                    "instructor": teacher,
                    "capacity": 30,
                    "monthly_fee": Decimal(fee),
                    "academic_year": "2026/2027",
                },
            )
            if not group.academic_year:
                group.academic_year = "2026/2027"
                group.instructor = group.instructor or teacher
                group.save()
            # Two sessions a week, like a real timetable.
            for offset in (0, 1):
                weekday, start = slots[(slot_i + offset * 6) % len(slots)]
                GroupSchedule.objects.get_or_create(
                    group=group,
                    weekday=weekday,
                    start_time=start,
                    defaults={
                        "end_time": time((start.hour + 2) % 24, start.minute),
                        "room": rooms[slot_i % len(rooms)],
                    },
                )
            slot_i += 1

    groups = list(Group.objects.select_related("grade_subject__grade").all())
    say(f"groups: {len(groups)}  schedules: {GroupSchedule.objects.count()}")

    # ------------------------------------------------------------ students
    target = 320
    have = Student.objects.count()
    grade_pool = ["SEC1"] * 3 + ["SEC2"] * 3 + ["SEC3"] * 4 + ["PREP3"] * 2 + ["PREP2"]
    made = 0
    for _ in range(target - have):
        male = random.random() < 0.52
        first = random.choice(FIRST_M if male else FIRST_F)
        full_name = f"{first} {random.choice(FAMILY)} {random.choice(FAMILY)}"
        grade = grades[random.choice(grade_pool)]
        create_student(
            actor=admin,
            full_name=full_name,
            grade=grade,
            gender="MALE" if male else "FEMALE",
            phone=phone(),
            guardian_name=f"{random.choice(FIRST_M)} {full_name.split()[-1]}",
            guardian_phone=phone(),
            guardian_relation=random.choice(RELATIONS),
            school=random.choice(SCHOOLS),
            address="القاهرة",
            date_of_birth=date(2008, 1, 1) + timedelta(days=random.randint(0, 1800)),
            enrolled_on=TODAY - timedelta(days=random.randint(30, 400)),
            status=StudentStatus.ACTIVE,
        )
        made += 1
    say(f"students: {Student.objects.count()} (+{made})")

    # --------------------------------------------------------- assignments
    by_grade = {}
    for group in groups:
        by_grade.setdefault(group.grade_subject.grade_id, []).append(group)

    assigned = 0
    for student in Student.objects.all():
        options = by_grade.get(student.grade_id, [])
        if not options:
            continue
        # A student takes 1–3 subjects at the center, one group per subject.
        by_subject = {}
        for group in options:
            by_subject.setdefault(group.grade_subject_id, []).append(group)
        picks = random.sample(
            list(by_subject), min(len(by_subject), random.choice([1, 2, 2, 3, 3, 3, 4]))
        )
        for offering_id in picks:
            group = random.choice(by_subject[offering_id])
            try:
                assign_svc.assign_student(
                    student,
                    group,
                    actor=admin,
                    start_date=max(student.enrolled_on, TODAY - timedelta(days=120)),
                )
                assigned += 1
            except Exception:
                pass
    say(f"assignments: {assigned}")

    # --------------------------------------------------------------- cards
    blank = StudentCard.objects.filter(status="AVAILABLE").count()
    if blank < 40:
        generate_batch(200, batch="BATCH-2026-A")
    pool = list(StudentCard.objects.filter(status="AVAILABLE"))
    random.shuffle(pool)
    linked = 0
    for student in Student.objects.filter(cards_held__isnull=True):
        if not pool or random.random() > 0.93:
            continue
        assign_card(pool.pop(), student, actor=admin)
        linked += 1
    say(f"cards: {StudentCard.objects.count()} total, {linked} newly linked")

    # ------------------------------------------------------------- lessons
    start_range = TODAY - timedelta(days=56)
    end_range = TODAY + timedelta(days=14)
    for group in groups:
        generate_lessons(group, start_range, end_range, actor=admin)
    say(f"lessons: {Lesson.objects.count()}")

    seed_attendance(admin)
    seed_money(admin)


def seed_attendance(admin):
    """Fill in the past. Realistic, not perfect: absences and lateness exist."""
    from apps.students.models import AssignmentStatus, StudentGroupAssignment

    roster = {}
    for a in StudentGroupAssignment.objects.filter(
        status=AssignmentStatus.ACTIVE
    ).select_related("student"):
        roster.setdefault(a.group_id, []).append(a)

    now = timezone.now()
    rows = []
    past = Lesson.objects.filter(scheduled_start__lt=now).order_by("scheduled_start")
    completed = 0
    for lesson in past:
        for a in roster.get(lesson.group_id, []):
            if a.start_date > lesson.scheduled_start.date():
                continue
            roll = random.random()
            start = lesson.scheduled_start
            if roll < 0.86:
                status, state = AttendanceStatus.PRESENT, AttendanceState.CHECKED_OUT
                late = 0
                check_in = start - timedelta(minutes=random.randint(1, 12))
            elif roll < 0.94:
                status, state = AttendanceStatus.LATE, AttendanceState.CHECKED_OUT
                late = random.randint(6, 25)
                check_in = start + timedelta(minutes=late)
            else:
                rows.append(
                    Attendance(
                        lesson=lesson,
                        student=a.student,
                        assigned_group_id=lesson.group_id,
                        attended_group_id=lesson.group_id,
                        state=AttendanceState.ABSENT,
                        status=AttendanceStatus.ABSENT,
                        attendance_type=AttendanceType.NORMAL,
                        scan_source=ScanSource.SYSTEM,
                    )
                )
                continue
            check_out = lesson.scheduled_end - timedelta(minutes=random.randint(0, 8))
            rows.append(
                Attendance(
                    lesson=lesson,
                    student=a.student,
                    assigned_group_id=lesson.group_id,
                    attended_group_id=lesson.group_id,
                    state=state,
                    status=status,
                    attendance_type=AttendanceType.NORMAL,
                    scan_source=random.choice([ScanSource.HID, ScanSource.CAMERA]),
                    check_in_at=check_in,
                    check_out_at=check_out,
                    late_minutes=late,
                    duration_minutes=int(
                        (check_out - check_in).total_seconds() // 60
                    ),
                    checked_in_by=admin,
                )
            )
        completed += 1

    Attendance.objects.bulk_create(rows, batch_size=500)
    Lesson.objects.filter(scheduled_start__lt=now).update(
        status=LessonStatus.COMPLETED,
        actual_start_at=F("scheduled_start"),
        actual_end_at=F("scheduled_end"),
    )
    say(f"attendance: {len(rows)} records over {completed} lessons")


def seed_money(admin):
    """Three months of billing, collected the way a real front desk collects."""
    cashier = User.objects.filter(username="cashier").first() or admin
    months = [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]
    for month in months:
        pay.generate_monthly_charges(month, actor=admin)

    charges = list(
        MonthlyCharge.objects.select_related("student").order_by("billing_month", "pk")
    )
    paid = partial = 0
    for charge in charges:
        age = (TODAY.year - charge.billing_month.year) * 12 + (
            TODAY.month - charge.billing_month.month
        )
        # Older months are almost fully collected; the current month is not.
        full_odds = {2: 0.94, 1: 0.86, 0: 0.42}.get(age, 0.9)
        partial_odds = {2: 0.03, 1: 0.07, 0: 0.18}.get(age, 0.05)
        roll = random.random()
        if roll < full_odds:
            amount = charge.amount_due
        elif roll < full_odds + partial_odds:
            amount = (charge.amount_due * Decimal("0.5")).quantize(Decimal("0.01"))
        else:
            continue

        day = min(random.randint(2, 26), 28)
        when = timezone.make_aware(
            datetime.combine(
                date(charge.billing_month.year, charge.billing_month.month, day),
                time(random.randint(13, 20), random.choice([0, 15, 30, 45])),
            )
        )
        if when > timezone.now():
            when = timezone.now() - timedelta(hours=random.randint(1, 40))
        pay.record_payment(
            charge,
            amount,
            collected_by=cashier,
            method=random.choice(["CASH", "CASH", "CASH", "WALLET", "TRANSFER"]),
            paid_at=when,
        )
        if amount == charge.amount_due:
            paid += 1
        else:
            partial += 1

    say(f"charges: {len(charges)}  paid: {paid}  partial: {partial}")


if __name__ == "__main__":
    main()
    print("\nDemo center ready.")
