"""Declarative role → permission map (docs/05 §G.5).

`seed_roles` turns this into auth.Groups. Permissions belonging to apps that
have not been implemented yet are reported as missing and skipped, so this file
can describe the final target from day one.
"""

from .models import Role

_ACADEMIC_MODELS = [
    "educationalstage",
    "grade",
    "subject",
    "gradesubject",
    "instructor",
    "group",
    "groupschedule",
]


def _crud(app_label: str, models: list[str], actions=("add", "change", "delete", "view")):
    return [f"{app_label}.{action}_{model}" for model in models for action in actions]


def _view(app_label: str, models: list[str]):
    return _crud(app_label, models, actions=("view",))


CENTER_ADMIN_PERMS = [
    *_crud("academics", _ACADEMIC_MODELS),
    *_crud("students", ["student", "studentgroupassignment"]),
    *_crud("cards", ["studentcard", "cardassignment"]),
    *_crud("lessons", ["lesson"]),
    "lessons.open_lesson",
    "lessons.complete_lesson",
    "lessons.cancel_lesson",
    "lessons.generate_lessons",
    *_crud("attendance", ["attendance"], actions=("add", "change", "view")),
    *_view("attendance", ["attendanceevent"]),
    "attendance.scan_any_group",
    "attendance.approve_exceptional",
    "attendance.correct_attendance",
    *_crud("payments", ["monthlycharge"], actions=("add", "change", "view")),
    *_crud("payments", ["payment"], actions=("add", "view")),
    "payments.refund_payment",
    "payments.waive_charge",
    "payments.generate_charges",
    "reports.view_reports",
    "reports.view_financial_reports",
    "reports.export_reports",
    "core.view_auditlog",
    "core.change_setting",
    "core.view_setting",
    "accounts.view_user",
    "accounts.add_user",
    "accounts.change_user",
]

INSTRUCTOR_PERMS = [
    *_view("academics", _ACADEMIC_MODELS),
    *_view("students", ["student", "studentgroupassignment"]),
    *_crud("lessons", ["lesson"], actions=("add", "change", "view")),
    "lessons.open_lesson",
    "lessons.complete_lesson",
    "lessons.generate_lessons",
    *_crud("attendance", ["attendance"], actions=("add", "change", "view")),
    *_view("attendance", ["attendanceevent"]),
    "attendance.correct_attendance",
    "attendance.approve_exceptional",
    "reports.view_reports",
]

CASHIER_PERMS = [
    *_view("academics", _ACADEMIC_MODELS),
    *_view("students", ["student", "studentgroupassignment"]),
    *_crud("payments", ["monthlycharge"], actions=("add", "change", "view")),
    *_crud("payments", ["payment"], actions=("add", "view")),
    "payments.generate_charges",
    "reports.view_financial_reports",
    "reports.export_reports",
]

SCAN_OPERATOR_PERMS = [
    *_view("academics", ["group", "gradesubject", "subject", "grade"]),
    *_view("lessons", ["lesson"]),
    "lessons.open_lesson",
    *_crud("attendance", ["attendance"], actions=("add", "view")),
]

ROLE_PERMISSIONS: dict[str, list[str]] = {
    Role.SUPER_ADMIN: ["*"],  # every permission in the project
    Role.CENTER_ADMIN: CENTER_ADMIN_PERMS,
    Role.INSTRUCTOR: INSTRUCTOR_PERMS,
    Role.CASHIER: CASHIER_PERMS,
    Role.SCAN_OPERATOR: SCAN_OPERATOR_PERMS,
}

GROUP_NAMES = {role: role.value for role in Role}
