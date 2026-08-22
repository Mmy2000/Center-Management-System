"""Object-level scoping (docs/05 §G.5, TASK-009).

Instructors see only their own groups; every other role with the relevant view
permission sees everything. Models are resolved lazily so this module can be
imported before the academics/lessons/students apps ship.

Unauthorized *object* access returns an empty queryset — callers turn that into
a 404, never a 403, so the existence of another instructor's group is not
disclosed.
"""

from django.apps import apps


def _is_scoped(user) -> bool:
    """True when the user must be restricted to their own groups."""
    if not (user and user.is_authenticated):
        return True
    if user.is_super_admin or user.has_perm("attendance.scan_any_group"):
        return False
    return user.is_instructor


def instructor_for(user):
    return getattr(user, "instructor", None)


def visible_groups(user, queryset=None):
    Group = apps.get_model("academics", "Group")
    qs = Group.objects.all() if queryset is None else queryset

    if not (user and user.is_authenticated):
        return qs.none()
    if not _is_scoped(user):
        return qs

    instructor = instructor_for(user)
    if instructor is None:
        return qs.none()
    return qs.filter(instructor=instructor)


def visible_lessons(user, queryset=None):
    Lesson = apps.get_model("lessons", "Lesson")
    qs = Lesson.objects.all() if queryset is None else queryset

    if not (user and user.is_authenticated):
        return qs.none()
    if not _is_scoped(user):
        return qs

    instructor = instructor_for(user)
    if instructor is None:
        return qs.none()
    return qs.filter(group__instructor=instructor)


def visible_students(user, queryset=None):
    Student = apps.get_model("students", "Student")
    qs = Student.objects.all() if queryset is None else queryset

    if not (user and user.is_authenticated):
        return qs.none()
    if not _is_scoped(user):
        return qs

    instructor = instructor_for(user)
    if instructor is None:
        return qs.none()
    return qs.filter(
        assignments__status="ACTIVE",
        assignments__group__instructor=instructor,
    ).distinct()


def can_touch_lesson(user, lesson) -> bool:
    """Whether the user may open/scan/correct this particular lesson."""
    if not (user and user.is_authenticated):
        return False
    if not _is_scoped(user):
        return True
    instructor = instructor_for(user)
    return instructor is not None and lesson.group.instructor_id == instructor.pk
