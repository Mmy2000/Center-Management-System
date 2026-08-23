"""User management (TASK-010).

Rules that are not expressible as permissions:
  * only a SUPER_ADMIN may grant the SUPER_ADMIN role;
  * nobody may deactivate their own account;
  * the last active SUPER_ADMIN cannot be deactivated.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from apps.core.audit import diff, record, snapshot
from apps.core.http import DomainError, ajax
from apps.core.models import AuditAction
from apps.tenancy import quota

from .models import Role, User

AUDITED_FIELDS = ["username", "full_name", "phone", "role", "is_active", "must_change_password"]


def _serialize(user: User) -> dict:
    return {
        "id": user.pk,
        "username": user.username,
        "full_name": user.full_name,
        "phone": user.phone,
        "role": user.role,
        "role_display": user.get_role_display(),
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }


def _guard_role_grant(actor, role):
    if role == Role.SUPER_ADMIN and not actor.is_super_admin:
        raise DomainError(
            "ERR_FORBIDDEN_ROLE",
            _("لا يمكن منح دور مدير النظام."),
            status=403,
            field_errors={"role": [_("غير مسموح بمنح هذا الدور")]},
        )


def _guard_deactivation(actor, target, new_is_active):
    if new_is_active or target.is_active == new_is_active:
        return
    if target.pk == actor.pk:
        raise DomainError("ERR_SELF_DEACTIVATION", _("لا يمكن تعطيل حسابك الشخصي."), status=409)
    if target.role == Role.SUPER_ADMIN:
        remaining = (
            User.objects.filter(role=Role.SUPER_ADMIN, is_active=True).exclude(pk=target.pk).count()
        )
        if remaining == 0:
            raise DomainError(
                "ERR_LAST_SUPER_ADMIN",
                _("لا يمكن تعطيل آخر مدير نظام."),
                status=409,
            )


@ajax(methods=["GET", "POST"], perm="accounts.view_user", feature="users.management")
def users(request):
    if request.method == "GET":
        qs = User.objects.order_by("username")
        role = request.GET.get("role")
        if role:
            qs = qs.filter(role=role)
        if request.GET.get("q"):
            q = request.GET["q"]
            qs = qs.filter(Q(username__icontains=q) | Q(full_name__icontains=q))
        return {"results": [_serialize(u) for u in qs[:200]]}

    if not request.user.has_perm("accounts.add_user"):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

    quota.check("users")

    data = request.json
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") or Role.SCAN_OPERATOR

    field_errors = {}
    if not username:
        field_errors["username"] = [_("اسم المستخدم مطلوب")]
    elif User.objects.filter(username=username).exists():
        field_errors["username"] = [_("اسم المستخدم مستخدم بالفعل")]
    if role not in Role.values:
        field_errors["role"] = [_("دور غير معروف")]
    if field_errors:
        raise DomainError("ERR_VALIDATION", _("بيانات غير صحيحة"), field_errors=field_errors)

    _guard_role_grant(request.user, role)

    try:
        validate_password(password)
    except ValidationError as exc:
        raise DomainError(
            "ERR_VALIDATION",
            _("كلمة مرور ضعيفة"),
            field_errors={"password": [str(m) for m in exc.messages]},
        ) from exc

    with transaction.atomic():
        user = User.objects.create_user(
            username=username,
            password=password,
            role=role,
            full_name=(data.get("full_name") or "").strip(),
            phone=(data.get("phone") or "").strip(),
            must_change_password=True,
        )
        record(
            AuditAction.USER_CREATED,
            user,
            changes=snapshot(user, AUDITED_FIELDS),
            reason=data.get("reason", ""),
        )
    return {"user": _serialize(user)}


@ajax(methods=["PATCH"], perm="accounts.change_user", feature="users.management")
def user_detail(request, pk):
    target = get_object_or_404(User, pk=pk)
    data = request.json
    before = snapshot(target, AUDITED_FIELDS)

    if "role" in data:
        if data["role"] not in Role.values:
            raise DomainError(
                "ERR_VALIDATION",
                _("دور غير معروف"),
                field_errors={"role": [_("دور غير معروف")]},
            )
        _guard_role_grant(request.user, data["role"])
        target.role = data["role"]

    if "is_active" in data:
        _guard_deactivation(request.user, target, bool(data["is_active"]))
        target.is_active = bool(data["is_active"])

    for field in ("full_name", "phone"):
        if field in data:
            setattr(target, field, (data[field] or "").strip())

    if data.get("new_password"):
        try:
            validate_password(data["new_password"], user=target)
        except ValidationError as exc:
            raise DomainError(
                "ERR_VALIDATION",
                _("كلمة مرور ضعيفة"),
                field_errors={"new_password": [str(m) for m in exc.messages]},
            ) from exc
        target.set_password(data["new_password"])
        target.must_change_password = True

    with transaction.atomic():
        target.save()
        changes = diff(before, snapshot(target, AUDITED_FIELDS))
        if data.get("new_password"):
            changes["password"] = {"old": "***", "new": "***"}
        if changes:
            record(
                AuditAction.USER_UPDATED,
                target,
                changes=changes,
                reason=data.get("reason", ""),
            )
    return {"user": _serialize(target)}
