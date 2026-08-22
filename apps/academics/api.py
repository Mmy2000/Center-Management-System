"""Academic-structure endpoints (TASK-012 → 018).

Uniform CRUD: list + create on the collection, PATCH on the detail. The
``?stage=`` / ``?grade=`` / ``?offering=`` filters double as the feeders for the
dependent selects in the UI (docs/05 §H.4).
"""

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from apps.core.crud import create_object, list_response, update_object
from apps.core.http import DomainError, ajax

from . import forms, serializers
from .models import (
    EducationalStage,
    Grade,
    GradeSubject,
    Group,
    GroupSchedule,
    Instructor,
    Subject,
)


def _require(request, perm):
    """Write permission guard for endpoints whose GET is more permissive."""
    if not request.user.has_perm(perm):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)


def _active_only(request, queryset):
    if request.GET.get("active") == "1":
        return queryset.filter(is_active=True)
    return queryset


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #


@ajax(methods=["GET", "POST"], perm="academics.view_educationalstage")
def stages(request):
    if request.method == "GET":
        return list_response(
            _active_only(request, EducationalStage.objects.all()), serializers.stage_json
        )
    _require(request, "academics.add_educationalstage")
    return create_object(request, forms.StageForm, serializers.stage_json, key="stage")


@ajax(methods=["PATCH"], perm="academics.change_educationalstage")
def stage_detail(request, pk):
    stage = get_object_or_404(EducationalStage, pk=pk)
    return update_object(request, stage, forms.StageForm, serializers.stage_json, key="stage")


# --------------------------------------------------------------------------- #
# Grades
# --------------------------------------------------------------------------- #


@ajax(methods=["GET", "POST"], perm="academics.view_grade")
def grades(request):
    if request.method == "GET":
        qs = Grade.objects.select_related("stage")
        if request.GET.get("stage"):
            qs = qs.filter(stage_id=request.GET["stage"])
        return list_response(_active_only(request, qs), serializers.grade_json)
    _require(request, "academics.add_grade")
    return create_object(request, forms.GradeForm, serializers.grade_json, key="grade")


@ajax(methods=["PATCH"], perm="academics.change_grade")
def grade_detail(request, pk):
    grade = get_object_or_404(Grade.objects.select_related("stage"), pk=pk)
    return update_object(request, grade, forms.GradeForm, serializers.grade_json, key="grade")


# --------------------------------------------------------------------------- #
# Subjects
# --------------------------------------------------------------------------- #


@ajax(methods=["GET", "POST"], perm="academics.view_subject")
def subjects(request):
    if request.method == "GET":
        return list_response(_active_only(request, Subject.objects.all()), serializers.subject_json)
    _require(request, "academics.add_subject")
    return create_object(request, forms.SubjectForm, serializers.subject_json, key="subject")


@ajax(methods=["PATCH"], perm="academics.change_subject")
def subject_detail(request, pk):
    subject = get_object_or_404(Subject, pk=pk)
    return update_object(
        request, subject, forms.SubjectForm, serializers.subject_json, key="subject"
    )


# --------------------------------------------------------------------------- #
# Offerings (GradeSubject)
# --------------------------------------------------------------------------- #


@ajax(methods=["GET", "POST"], perm="academics.view_gradesubject")
def offerings(request):
    if request.method == "GET":
        qs = GradeSubject.objects.select_related("grade", "subject", "grade__stage")
        if request.GET.get("grade"):
            qs = qs.filter(grade_id=request.GET["grade"])
        if request.GET.get("subject"):
            qs = qs.filter(subject_id=request.GET["subject"])
        if request.GET.get("stage"):
            qs = qs.filter(grade__stage_id=request.GET["stage"])
        return list_response(_active_only(request, qs), serializers.offering_json)
    _require(request, "academics.add_gradesubject")
    return create_object(request, forms.GradeSubjectForm, serializers.offering_json, key="offering")


@ajax(methods=["PATCH"], perm="academics.change_gradesubject")
def offering_detail(request, pk):
    offering = get_object_or_404(GradeSubject.objects.select_related("grade", "subject"), pk=pk)
    return update_object(
        request, offering, forms.GradeSubjectForm, serializers.offering_json, key="offering"
    )


# --------------------------------------------------------------------------- #
# Instructors
# --------------------------------------------------------------------------- #


@ajax(methods=["GET", "POST"], perm="academics.view_instructor")
def instructors(request):
    if request.method == "GET":
        return list_response(
            _active_only(request, Instructor.objects.all()), serializers.instructor_json
        )
    _require(request, "academics.add_instructor")
    return create_object(
        request, forms.InstructorForm, serializers.instructor_json, key="instructor"
    )


@ajax(methods=["PATCH"], perm="academics.change_instructor")
def instructor_detail(request, pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    return update_object(
        request, instructor, forms.InstructorForm, serializers.instructor_json, key="instructor"
    )


# --------------------------------------------------------------------------- #
# Groups
# --------------------------------------------------------------------------- #


def _group_queryset(request):
    from apps.accounts.scoping import visible_groups

    qs = visible_groups(
        request.user,
        Group.objects.select_related(
            "grade_subject__subject", "grade_subject__grade__stage", "instructor"
        ),
    )
    params = request.GET
    if params.get("offering"):
        qs = qs.filter(grade_subject_id=params["offering"])
    if params.get("grade"):
        qs = qs.filter(grade_subject__grade_id=params["grade"])
    if params.get("stage"):
        qs = qs.filter(grade_subject__grade__stage_id=params["stage"])
    if params.get("subject"):
        qs = qs.filter(grade_subject__subject_id=params["subject"])
    if params.get("instructor"):
        qs = qs.filter(instructor_id=params["instructor"])
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("q"):
        qs = qs.filter(name__icontains=params["q"])
    return qs


@ajax(methods=["GET", "POST"], perm="academics.view_group")
def groups(request):
    if request.method == "GET":
        return list_response(_group_queryset(request), serializers.group_json)
    _require(request, "academics.add_group")
    return create_object(request, forms.GroupForm, serializers.group_json, key="group")


@ajax(methods=["PATCH"], perm="academics.change_group")
def group_detail(request, pk):
    group = get_object_or_404(_group_queryset(request), pk=pk)
    return update_object(request, group, forms.GroupForm, serializers.group_json, key="group")


# --------------------------------------------------------------------------- #
# Weekly schedules
# --------------------------------------------------------------------------- #


@ajax(methods=["GET", "POST"], perm="academics.view_groupschedule")
def group_schedules(request, pk):
    group = get_object_or_404(_group_queryset(request), pk=pk)
    if request.method == "GET":
        return list_response(group.schedules.all(), serializers.schedule_json)
    _require(request, "academics.add_groupschedule")
    request.json["group"] = group.pk
    return create_object(
        request, forms.GroupScheduleForm, serializers.schedule_json, key="schedule"
    )


@ajax(methods=["DELETE"], perm="academics.delete_groupschedule")
def schedule_detail(request, pk):
    schedule = get_object_or_404(GroupSchedule, pk=pk)
    schedule.delete()
    return {"deleted": pk}
