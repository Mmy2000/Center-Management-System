import json

import pytest
from django.core.exceptions import ValidationError

from apps.core.http import DomainError, ajax


@ajax(methods=["GET"])
def sample_get(request):
    return {"value": 42}


@ajax(methods=["POST"])
def sample_post(request):
    if request.json.get("boom"):
        raise DomainError("ERR_BOOM", "انفجار", status=409, data={"hint": "X"})
    if request.json.get("invalid"):
        raise ValidationError({"amount": ["المبلغ يجب أن يكون أكبر من صفر"]})
    return {"echo": request.json}


@ajax(methods=["GET"], perm="payments.add_payment")
def sample_perm(request):
    return {"ok": True}


@ajax(methods=["GET"], login_required=False)
def sample_public(request):
    return {"public": True}


def _payload(response):
    return json.loads(response.content.decode("utf-8"))


@pytest.mark.django_db
def test_success_envelope(rf, user_factory):
    request = rf.get("/x/")
    request.user = user_factory(username="u1")
    response = sample_get(request)
    assert response.status_code == 200
    assert _payload(response) == {"ok": True, "code": "OK", "data": {"value": 42}}
    assert "X-Response-Time-ms" in response


@pytest.mark.django_db
def test_method_guard(rf, user_factory):
    request = rf.post("/x/")
    request.user = user_factory(username="u2")
    response = sample_get(request)
    assert response.status_code == 405
    assert _payload(response)["code"] == "ERR_METHOD_NOT_ALLOWED"


def test_login_required(rf):
    from django.contrib.auth.models import AnonymousUser

    request = rf.get("/x/")
    request.user = AnonymousUser()
    response = sample_get(request)
    assert response.status_code == 403
    assert _payload(response)["code"] == "ERR_AUTH_REQUIRED"


def test_public_endpoint_allows_anonymous(rf):
    from django.contrib.auth.models import AnonymousUser

    request = rf.get("/x/")
    request.user = AnonymousUser()
    assert sample_public(request).status_code == 200


@pytest.mark.django_db
def test_permission_guard(rf, user_factory):
    from apps.accounts.models import Role

    request = rf.get("/x/")
    request.user = user_factory(username="op", role=Role.SCAN_OPERATOR)
    response = sample_perm(request)
    assert response.status_code == 403
    assert _payload(response)["code"] == "ERR_FORBIDDEN"


@pytest.mark.django_db
def test_domain_error_maps_to_envelope(rf, user_factory):
    request = rf.post("/x/", data=json.dumps({"boom": True}), content_type="application/json")
    request.user = user_factory(username="u3")
    response = sample_post(request)
    body = _payload(response)
    assert response.status_code == 409
    assert body["ok"] is False
    assert body["code"] == "ERR_BOOM"
    assert body["data"] == {"hint": "X"}


@pytest.mark.django_db
def test_validation_error_becomes_field_errors(rf, user_factory):
    request = rf.post("/x/", data=json.dumps({"invalid": True}), content_type="application/json")
    request.user = user_factory(username="u4")
    response = sample_post(request)
    body = _payload(response)
    assert response.status_code == 400
    assert body["code"] == "ERR_VALIDATION"
    assert body["field_errors"]["amount"] == ["المبلغ يجب أن يكون أكبر من صفر"]


@pytest.mark.django_db
def test_malformed_json_rejected(rf, user_factory):
    request = rf.post("/x/", data="{not json", content_type="application/json")
    request.user = user_factory(username="u5")
    response = sample_post(request)
    assert response.status_code == 400
    assert _payload(response)["code"] == "ERR_INVALID_JSON"


@pytest.mark.django_db
def test_json_body_is_parsed(rf, user_factory):
    request = rf.post("/x/", data=json.dumps({"a": 1}), content_type="application/json")
    request.user = user_factory(username="u6")
    assert _payload(sample_post(request))["data"]["echo"] == {"a": 1}


@ajax(methods=["POST"])
def sample_upload(request):
    upload = request.FILES.get("file")
    return {"name": upload.name if upload else None, "batch": request.json.get("batch")}


@pytest.mark.django_db
def test_multipart_uploads_reach_the_view(rf, user_factory):
    """Regression: reading request.body on a multipart POST raises
    RawPostDataException, because the CSRF middleware already consumed the
    stream to find the token."""
    from io import BytesIO

    payload = BytesIO(b"card_number,qr_token\nCARD-1,CMS1:tok\n")
    payload.name = "batch.csv"

    request = rf.post("/x/", {"file": payload, "batch": "B1"})
    request.user = user_factory(username="uploader")

    response = sample_upload(request)
    assert response.status_code == 200
    body = _payload(response)["data"]
    assert body["name"] == "batch.csv"
    assert body["batch"] == "B1"
