"""Seed the academic hierarchy (TASK-019).

Idempotent: every object is created with ``get_or_create``, so running it twice
changes nothing. ``--demo`` builds the example tree from the brief
(Secondary → 3 grades → Physics/Maths → groups A/B/C).
"""

from datetime import time
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.models import (
    EducationalStage,
    Grade,
    GradeSubject,
    Group,
    GroupSchedule,
    Instructor,
    Subject,
    Weekday,
)

STAGES = [
    ("Primary", "الابتدائية", "PRI", 1),
    ("Preparatory", "الإعدادية", "PREP", 2),
    ("Secondary", "الثانوية", "SEC", 3),
]

GRADES = {
    "PRI": [
        ("Grade 4 Primary", "الرابع الابتدائي", "PRI4", 1),
        ("Grade 5 Primary", "الخامس الابتدائي", "PRI5", 2),
        ("Grade 6 Primary", "السادس الابتدائي", "PRI6", 3),
    ],
    "PREP": [
        ("Grade 1 Preparatory", "الأول الإعدادي", "PREP1", 1),
        ("Grade 2 Preparatory", "الثاني الإعدادي", "PREP2", 2),
        ("Grade 3 Preparatory", "الثالث الإعدادي", "PREP3", 3),
    ],
    "SEC": [
        ("Grade 1 Secondary", "الأول الثانوي", "SEC1", 1),
        ("Grade 2 Secondary", "الثاني الثانوي", "SEC2", 2),
        ("Grade 3 Secondary", "الثالث الثانوي", "SEC3", 3),
    ],
}

SUBJECTS = [
    ("Mathematics", "الرياضيات", "MATH", "#2b6cb0"),
    ("Physics", "الفيزياء", "PHY", "#c05621"),
    ("Chemistry", "الكيمياء", "CHEM", "#2f855a"),
    ("Biology", "الأحياء", "BIO", "#6b46c1"),
    ("Arabic", "اللغة العربية", "AR", "#b7791f"),
    ("English", "اللغة الإنجليزية", "EN", "#2c7a7b"),
]

# --demo: (grade_code, subject_code, fee, [group names], (weekday, start, end))
DEMO_OFFERINGS = [
    (
        "SEC1",
        "PHY",
        400,
        ["Group A", "Group B", "Group C"],
        (Weekday.SATURDAY, time(16, 0), time(18, 0)),
    ),
    ("SEC1", "MATH", 400, ["Group A", "Group B"], (Weekday.MONDAY, time(16, 0), time(18, 0))),
    (
        "SEC2",
        "PHY",
        450,
        ["Group A", "Group B", "Group C"],
        (Weekday.SUNDAY, time(16, 0), time(18, 0)),
    ),
    (
        "SEC3",
        "PHY",
        500,
        ["Group A", "Group B", "Group C"],
        (Weekday.TUESDAY, time(16, 0), time(18, 0)),
    ),
    ("SEC3", "CHEM", 500, ["Group A", "Group B"], (Weekday.THURSDAY, time(16, 0), time(18, 0))),
]

DEMO_INSTRUCTORS = [
    ("أ. محمد عبد الرحمن", "01001234567", ["PHY"]),
    ("أ. هدى السيد", "01112345678", ["MATH"]),
    ("أ. كريم فؤاد", "01223456789", ["CHEM", "BIO"]),
]


class Command(BaseCommand):
    help = "Seed educational stages, grades and subjects (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="Also build the example tree.")
        parser.add_argument("--force", action="store_true", help="Allow --demo with DEBUG=False.")

    @transaction.atomic
    def handle(self, *args, **options):
        from django.conf import settings as django_settings

        verbosity = options.get("verbosity", 1)
        if options["demo"] and not django_settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to seed demo data with DEBUG=False (use --force).")

        stages = {}
        for name, name_ar, code, order in STAGES:
            stage, _ = EducationalStage.objects.get_or_create(
                code=code, defaults={"name": name, "name_ar": name_ar, "order": order}
            )
            stages[code] = stage

        grades = {}
        for stage_code, rows in GRADES.items():
            for name, name_ar, code, order in rows:
                grade, _ = Grade.objects.get_or_create(
                    code=code,
                    defaults={
                        "stage": stages[stage_code],
                        "name": name,
                        "name_ar": name_ar,
                        "order": order,
                    },
                )
                grades[code] = grade

        subjects = {}
        for name, name_ar, code, color in SUBJECTS:
            subject, _ = Subject.objects.get_or_create(
                code=code, defaults={"name": name, "name_ar": name_ar, "color": color}
            )
            subjects[code] = subject

        if verbosity >= 1:
            self.stdout.write(
                f"stages={EducationalStage.objects.count()} "
                f"grades={Grade.objects.count()} subjects={Subject.objects.count()}"
            )

        if options["demo"]:
            self._seed_demo(grades, subjects, verbosity)

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Academic structure seeded."))

    def _seed_demo(self, grades, subjects, verbosity):
        instructors = {}
        for full_name, phone, subject_codes in DEMO_INSTRUCTORS:
            instructor, _ = Instructor.objects.get_or_create(
                full_name=full_name, defaults={"phone": phone}
            )
            instructor.subjects.set([subjects[code] for code in subject_codes])
            for code in subject_codes:
                instructors.setdefault(code, instructor)

        for grade_code, subject_code, fee, group_names, slot in DEMO_OFFERINGS:
            offering, _ = GradeSubject.objects.get_or_create(
                grade=grades[grade_code],
                subject=subjects[subject_code],
                defaults={"default_monthly_fee": Decimal(fee)},
            )
            weekday, start, end = slot
            for index, group_name in enumerate(group_names):
                group, _ = Group.objects.get_or_create(
                    code=f"{grade_code}-{subject_code}-{group_name[-1]}",
                    defaults={
                        "grade_subject": offering,
                        "name": group_name,
                        "name_ar": f"مجموعة {group_name[-1]}",
                        "instructor": instructors.get(subject_code),
                        "capacity": 35,
                        "monthly_fee": offering.default_monthly_fee,
                    },
                )
                # Stagger the groups two hours apart on the same weekday.
                start_hour = (start.hour + index * 2) % 24
                GroupSchedule.objects.get_or_create(
                    group=group,
                    weekday=weekday,
                    start_time=time(start_hour, start.minute),
                    defaults={"end_time": time((start_hour + 2) % 24, end.minute)},
                )

        if verbosity >= 1:
            self.stdout.write(
                f"offerings={GradeSubject.objects.count()} groups={Group.objects.count()} "
                f"schedules={GroupSchedule.objects.count()}"
            )
