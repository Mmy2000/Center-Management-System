import datetime
from decimal import Decimal

from apps.core import audit
from apps.core.models import AuditAction, AuditLog


def test_diff_reports_only_changed_fields():
    before = {"name": "أحمد", "status": "ACTIVE", "fee": "500.00"}
    after = {"name": "أحمد", "status": "SUSPENDED", "fee": "500.00"}
    assert audit.diff(before, after) == {"status": {"old": "ACTIVE", "new": "SUSPENDED"}}


def test_jsonable_coerces_awkward_types():
    assert audit.jsonable(Decimal("12.50")) == "12.50"
    assert audit.jsonable(datetime.date(2026, 8, 22)) == "2026-08-22"
    assert audit.jsonable(None) is None
    assert audit.jsonable({"a": Decimal("1")}) == {"a": "1"}


def test_record_without_request_context_has_null_actor(db):
    audit.clear_context()
    entry = audit.record(AuditAction.SETTING_CHANGED, object_repr="billing.due_day_of_month")
    assert entry.actor is None
    assert entry.ip_address is None
    assert AuditLog.objects.count() == 1


def test_record_uses_thread_local_actor_and_ip(db, user_factory):
    user = user_factory(username="cashier1")
    audit.set_context(user=user, ip="10.0.0.9", user_agent="pytest")
    try:
        entry = audit.record(
            AuditAction.PAYMENT_CREATED,
            user,
            changes={"amount": {"old": None, "new": "300.00"}},
            reason="دفعة نقدية",
        )
    finally:
        audit.clear_context()

    assert entry.actor == user
    assert entry.ip_address == "10.0.0.9"
    assert entry.user_agent == "pytest"
    assert entry.reason == "دفعة نقدية"
    assert entry.object_id == user.pk
    assert entry.content_type.model == "user"


def test_snapshot_reads_named_fields(db, user_factory):
    user = user_factory(username="snap")
    assert audit.snapshot(user, ["username", "role"]) == {
        "username": "snap",
        "role": user.role,
    }


def test_client_ip_prefers_forwarded_header(rf):
    request = rf.get("/", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1", REMOTE_ADDR="10.0.0.1")
    assert audit.client_ip(request) == "203.0.113.7"
    plain = rf.get("/", REMOTE_ADDR="10.0.0.5")
    assert audit.client_ip(plain) == "10.0.0.5"
