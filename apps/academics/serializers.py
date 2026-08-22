"""Plain dict serializers (no DRF — docs/README stack decisions)."""


def stage_json(stage):
    return {
        "id": stage.pk,
        "name": stage.name,
        "name_ar": stage.name_ar,
        "code": stage.code,
        "order": stage.order,
        "is_active": stage.is_active,
        "label": str(stage),
    }


def grade_json(grade):
    return {
        "id": grade.pk,
        "stage_id": grade.stage_id,
        "stage": str(grade.stage),
        "name": grade.name,
        "name_ar": grade.name_ar,
        "code": grade.code,
        "order": grade.order,
        "is_active": grade.is_active,
        "label": str(grade),
    }


def subject_json(subject):
    return {
        "id": subject.pk,
        "name": subject.name,
        "name_ar": subject.name_ar,
        "code": subject.code,
        "color": subject.color,
        "is_active": subject.is_active,
        "label": str(subject),
    }


def offering_json(offering):
    return {
        "id": offering.pk,
        "grade_id": offering.grade_id,
        "grade": str(offering.grade),
        "subject_id": offering.subject_id,
        "subject": str(offering.subject),
        "default_monthly_fee": str(offering.default_monthly_fee),
        "is_active": offering.is_active,
        "label": str(offering),
    }


def instructor_json(instructor):
    return {
        "id": instructor.pk,
        "full_name": instructor.full_name,
        "phone": instructor.phone,
        "user_id": instructor.user_id,
        "is_active": instructor.is_active,
        "label": str(instructor),
    }


def group_json(group):
    return {
        "id": group.pk,
        "name": group.name,
        "name_ar": group.name_ar,
        "code": group.code,
        "grade_subject_id": group.grade_subject_id,
        "subject": str(group.grade_subject.subject),
        "grade": str(group.grade_subject.grade),
        "stage": str(group.grade_subject.grade.stage),
        "instructor_id": group.instructor_id,
        "instructor": str(group.instructor) if group.instructor_id else None,
        "capacity": group.capacity,
        "monthly_fee": str(group.monthly_fee),
        "academic_year": group.academic_year,
        "status": group.status,
        "status_display": group.get_status_display(),
        "label": str(group),
    }


def schedule_json(schedule):
    return {
        "id": schedule.pk,
        "group_id": schedule.group_id,
        "weekday": schedule.weekday,
        "weekday_display": schedule.get_weekday_display(),
        "start_time": schedule.start_time.strftime("%H:%M"),
        "end_time": schedule.end_time.strftime("%H:%M"),
        "room": schedule.room,
        "is_active": schedule.is_active,
        "label": str(schedule),
    }
