"""JSON envelope + the ``@ajax`` decorator (docs/05 §G.2).

Success:  {"ok": true,  "code": "OK", "data": {...}, "message": "..."}
Failure:  {"ok": false, "code": "ERR_...", "message": "...", "field_errors": {...}}

Views decorated with :func:`ajax` may simply ``return {...}`` (wrapped as data)
or raise :class:`DomainError`; method, permission, body parsing, error mapping
and timing are all handled here so views stay free of boilerplate.
"""

import json
import logging
import time
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """A business-rule failure that maps onto the JSON error envelope."""

    def __init__(self, code, message="", *, status=400, field_errors=None, data=None):
        self.code = code
        self.message = str(message) if message else code
        self.status = status
        self.field_errors = field_errors or {}
        self.data = data or {}
        super().__init__(self.message)


def ok(data=None, *, code="OK", message="", status=200):
    payload = {"ok": True, "code": code, "data": data if data is not None else {}}
    if message:
        payload["message"] = str(message)
    return JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})


def fail(code, message="", *, status=400, field_errors=None, data=None):
    payload = {"ok": False, "code": code, "message": str(message) if message else code}
    if field_errors:
        payload["field_errors"] = field_errors
    if data:
        payload["data"] = data
    return JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})


def _validation_field_errors(exc: ValidationError) -> dict:
    if hasattr(exc, "message_dict"):
        return {k: [str(m) for m in v] for k, v in exc.message_dict.items()}
    return {"__all__": [str(m) for m in exc.messages]}


def ajax(methods=("GET",), *, perm=None, feature=None, login_required=True):
    """Wrap a view into the JSON contract.

    ``request.json`` holds the parsed body for JSON requests (``{}`` otherwise).

    ``perm`` and ``feature`` are orthogonal and **both** must pass: one asks
    what this *user* may do, the other what this *center* bought. Never conflate
    them by editing the permission matrix per tenant — the matrix is identical
    everywhere, and the feature check is what differs.

    A disabled feature answers 404, not 403: a center that did not buy payments
    should not learn that the payments endpoint exists.
    """

    allowed = {m.upper() for m in methods}

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            started = time.monotonic()

            if request.method not in allowed:
                return fail(
                    "ERR_METHOD_NOT_ALLOWED",
                    _("طريقة الطلب غير مسموح بها"),
                    status=405,
                )

            user = getattr(request, "user", None)
            if login_required and not (user and user.is_authenticated):
                return fail("ERR_AUTH_REQUIRED", _("يجب تسجيل الدخول"), status=403)

            if perm and not (user and user.has_perm(perm)):
                return fail("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

            if feature:
                from apps.tenancy.resolver import has_feature

                if not has_feature(feature):
                    return fail(
                        "ERR_FEATURE_DISABLED",
                        _("هذه الخاصية غير مُفعّلة في هذا الحساب"),
                        status=404,
                    )

            request.json = {}
            content_type = (request.content_type or "").lower()
            if request.method != "GET":
                if "multipart/form-data" in content_type:
                    # Never touch request.body here: the CSRF middleware has
                    # already consumed the stream to find the token, and reading
                    # it again raises RawPostDataException. Uploads read
                    # request.FILES / request.POST directly.
                    request.json = request.POST.dict()
                elif "application/json" in content_type:
                    if request.body:
                        try:
                            parsed = json.loads(request.body.decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            return fail(
                                "ERR_INVALID_JSON", _("صيغة البيانات غير صحيحة"), status=400
                            )
                        if not isinstance(parsed, dict):
                            return fail(
                                "ERR_INVALID_JSON", _("صيغة البيانات غير صحيحة"), status=400
                            )
                        request.json = parsed
                else:
                    request.json = request.POST.dict()

            try:
                result = view(request, *args, **kwargs)
            except DomainError as exc:
                return fail(
                    exc.code,
                    exc.message,
                    status=exc.status,
                    field_errors=exc.field_errors,
                    data=exc.data,
                )
            except ValidationError as exc:
                return fail(
                    "ERR_VALIDATION",
                    _("بيانات غير صحيحة"),
                    status=400,
                    field_errors=_validation_field_errors(exc),
                )
            except PermissionDenied:
                return fail("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)
            except Http404:
                return fail("ERR_NOT_FOUND", _("غير موجود"), status=404)

            elapsed_ms = int((time.monotonic() - started) * 1000)
            if isinstance(result, dict):
                response = ok(result)
            else:
                response = result
            response["X-Response-Time-ms"] = str(elapsed_ms)
            return response

        return wrapper

    return decorator
