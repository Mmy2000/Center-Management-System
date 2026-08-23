"""The scanner contract in one place (docs/03 §D.6).

Every code maps to an HTTP status, a severity, a colour, a sound and a
bilingual message, so the UI, the tests and the docs cannot drift apart.
"""

from django.utils.translation import gettext_lazy as _

OK = "OK"
WARN = "WARN"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
ERR = "ERR"

# severity -> (colour, sound)
PRESENTATION = {
    OK: ("green", "beep"),
    WARN: ("amber", "double-beep"),
    NEEDS_APPROVAL: ("amber", "double-beep"),
    ERR: ("red", "buzz"),
}


def _row(severity, status, ar, en):
    return {"severity": severity, "http_status": status, "ar": ar, "en": en}


CODES: dict[str, dict] = {
    # ------------------------------- success ------------------------------ #
    "OK_CHECK_IN": _row(OK, 200, _("تم تسجيل الحضور"), "Check-in successful"),
    "OK_CHECK_IN_LATE": _row(OK, 200, _("تم تسجيل الحضور — متأخر"), "Check-in successful — late"),
    "OK_CHECK_IN_ALTERNATIVE": _row(
        OK, 200, _("تم تسجيل الحضور — مجموعة بديلة"), "Check-in — alternative group"
    ),
    "OK_CHECK_IN_MAKEUP": _row(
        OK, 200, _("تم تسجيل الحضور — حصة تعويضية"), "Check-in — make-up lesson"
    ),
    "OK_CHECK_OUT": _row(OK, 200, _("تم تسجيل الانصراف"), "Check-out successful"),
    "OK_CHECK_OUT_PARTIAL": _row(
        OK, 200, _("تم الانصراف — حضور جزئي"), "Check-out — partial attendance"
    ),
    # ------------------------------ warnings ------------------------------ #
    "WARN_DUPLICATE": _row(WARN, 200, _("تم تسجيل الحضور بالفعل"), "Already checked in"),
    "WARN_ALREADY_CHECKED_OUT": _row(
        WARN, 200, _("الطالب سجّل الانصراف بالفعل"), "Already checked out"
    ),
    "WARN_NOT_ASSIGNED": _row(
        WARN, 200, _("الطالب غير مقيد بهذه المادة"), "Student is not assigned to this subject"
    ),
    "WARN_PAYMENT_DUE": _row(WARN, 200, _("عليه مستحقات مالية"), "Payment outstanding"),
    "WARN_CAPACITY": _row(WARN, 200, _("المجموعة تجاوزت السعة"), "Group over capacity"),
    "WARN_OUTSIDE_WINDOW": _row(
        WARN,
        200,
        _("خارج وقت التسجيل — تم التسجيل استثنائيًا"),
        "Outside window — recorded as exceptional",
    ),
    # ---------------------------- needs approval -------------------------- #
    "NEEDS_APPROVAL_NOT_ASSIGNED": _row(
        NEEDS_APPROVAL,
        200,
        _("يحتاج اعتماد المشرف — غير مقيد بالمادة"),
        "Supervisor approval required — not assigned",
    ),
    "NEEDS_APPROVAL_GRADE": _row(
        NEEDS_APPROVAL,
        200,
        _("يحتاج اعتماد المشرف — صف مختلف"),
        "Supervisor approval required — different grade",
    ),
    "NEEDS_APPROVAL_WINDOW": _row(
        NEEDS_APPROVAL,
        200,
        _("يحتاج اعتماد المشرف — خارج الوقت"),
        "Supervisor approval required — outside window",
    ),
    "NEEDS_APPROVAL_PAYMENT": _row(
        NEEDS_APPROVAL,
        200,
        _("يحتاج اعتماد المشرف — مستحقات مالية"),
        "Supervisor approval required — unpaid",
    ),
    # ------------------------------- errors ------------------------------- #
    "ERR_CARD_NOT_FOUND": _row(ERR, 404, _("بطاقة غير معروفة"), "Unknown card"),
    "ERR_CARD_UNASSIGNED": _row(
        ERR, 409, _("هذه البطاقة غير مرتبطة بطالب"), "Card is not linked to a student"
    ),
    "ERR_CARD_LOST": _row(ERR, 409, _("هذه البطاقة مبلّغ عن فقدها"), "Card reported lost"),
    "ERR_CARD_DISABLED": _row(ERR, 409, _("هذه البطاقة معطّلة"), "Card disabled"),
    "ERR_CARD_REPLACED": _row(ERR, 409, _("هذه البطاقة تم استبدالها"), "Card replaced"),
    "ERR_STUDENT_INACTIVE": _row(ERR, 409, _("الطالب غير نشط"), "Student inactive"),
    "ERR_STUDENT_SUSPENDED": _row(ERR, 409, _("الطالب موقوف"), "Student suspended"),
    "ERR_LESSON_NOT_FOUND": _row(ERR, 404, _("الحصة غير موجودة"), "Lesson not found"),
    "ERR_LESSON_NOT_OPEN": _row(ERR, 409, _("الحصة غير مفتوحة للتسجيل"), "Lesson is not open"),
    "ERR_LESSON_CANCELLED": _row(ERR, 409, _("الحصة ملغاة"), "Lesson cancelled"),
    "ERR_WINDOW_CLOSED": _row(ERR, 409, _("انتهى وقت تسجيل الحضور"), "Attendance window closed"),
    "ERR_NOT_CHECKED_IN": _row(ERR, 409, _("لم يتم تسجيل الحضور أولًا"), "No check-in to close"),
    "ERR_PAYMENT_BLOCKED": _row(ERR, 409, _("ممنوع الحضور لعدم السداد"), "Blocked: payment due"),
    "ERR_NOT_ASSIGNED_BLOCKED": _row(
        ERR, 409, _("الطالب غير مقيد بهذه المادة"), "Blocked: not assigned"
    ),
    "ERR_GRADE_BLOCKED": _row(ERR, 409, _("الطالب من صف مختلف"), "Blocked: different grade"),
    "ERR_ATTENDANCE_CANCELLED": _row(
        ERR, 409, _("سجل الحضور ملغي — يلزم إعادة تفعيله"), "Attendance record cancelled"
    ),
    "ERR_RATE_LIMITED": _row(ERR, 429, _("عدد عمليات كبير"), "Rate limited"),
    "ERR_SERVER": _row(ERR, 500, _("خطأ غير متوقع"), "Unexpected error"),
}


def info(code: str) -> dict:
    try:
        row = CODES[code]
    except KeyError as exc:  # a code without a message must fail loudly
        raise KeyError(f"Unknown result code: {code!r}") from exc
    colour, sound = PRESENTATION[row["severity"]]
    return {
        "code": code,
        "severity": row["severity"],
        "http_status": row["http_status"],
        "message": str(row["ar"]),
        "message_en": row["en"],
        "colour": colour,
        "sound": sound,
    }


def is_success(code: str) -> bool:
    return CODES[code]["severity"] in (OK, WARN, NEEDS_APPROVAL)
