"""Console forms (TASK-113/114/116)."""

from django import forms
from django.conf import settings

from apps.tenancy.constants import FeatureState
from apps.tenancy.models import Plan, Tenant, validate_slug


class TenantCreateForm(forms.Form):
    """The provisioning wizard, as one form over ``services.provision_tenant``.

    One form rather than three steps with session state: creating a client is a
    single transaction, and a wizard that can be abandoned halfway invites
    exactly the half-built tenant the service refuses to leave behind.
    """

    name = forms.CharField(label="اسم السنتر", max_length=150)
    slug = forms.CharField(
        label="المعرّف (النطاق الفرعي)",
        max_length=32,
        validators=[validate_slug],
        help_text="حروف إنجليزية صغيرة وأرقام وشرطات. لا يمكن تغييره بعد الإنشاء.",
    )
    plan = forms.ModelChoiceField(label="الباقة", queryset=Plan.objects.all())
    trial_days = forms.IntegerField(
        label="أيام التجربة", min_value=0, max_value=365, initial=30, required=False
    )

    owner_username = forms.CharField(label="اسم مستخدم المسؤول", max_length=150, initial="admin")
    owner_name = forms.CharField(label="اسم المسؤول", max_length=150, required=False)
    owner_email = forms.EmailField(label="بريد المسؤول", required=False)
    owner_phone = forms.CharField(label="هاتف المسؤول", max_length=20, required=False)

    seed_academics = forms.BooleanField(
        label="تجهيز المراحل والصفوف الافتراضية", required=False, initial=True
    )
    demo = forms.BooleanField(label="بيانات تجريبية (للعرض فقط)", required=False)

    def clean_slug(self):
        slug = (self.cleaned_data["slug"] or "").strip().lower()
        if Tenant.objects.filter(slug=slug).exists():
            raise forms.ValidationError("هذا المعرّف مستخدم بالفعل.")
        from apps.tenancy.models import Domain

        from .services import host_for

        host = host_for(slug, settings.TENANT_BASE_DOMAIN)
        if Domain.objects.filter(host=host).exists():
            raise forms.ValidationError(f"النطاق الناتج مستخدم بالفعل: {host}")
        return slug

    def clean(self):
        data = super().clean()
        if data.get("demo") and not data.get("seed_academics"):
            self.add_error("demo", "البيانات التجريبية تحتاج تجهيز المراحل والصفوف.")
        return data


class ReasonForm(forms.Form):
    """Every lifecycle change is typed, so the audit row can answer "why"."""

    reason = forms.CharField(label="السبب", widget=forms.Textarea(attrs={"rows": 3}))

    def clean_reason(self):
        reason = (self.cleaned_data["reason"] or "").strip()
        if len(reason) < 4:
            raise forms.ValidationError("اكتب سببًا واضحًا.")
        return reason


class PlanChangeForm(forms.Form):
    plan = forms.ModelChoiceField(label="الباقة", queryset=Plan.objects.all())
    reason = forms.CharField(label="السبب", required=False, max_length=200)


class FeatureToggleForm(forms.Form):
    feature_key = forms.CharField(max_length=60)
    state = forms.ChoiceField(choices=FeatureState.choices)
    note = forms.CharField(max_length=200, required=False)


class ImpersonationForm(forms.Form):
    """Read-only unless the operator explicitly says otherwise, in writing."""

    mode = forms.ChoiceField(
        label="الوضع",
        choices=[("read", "قراءة فقط"), ("write", "قراءة وتعديل")],
        initial="read",
    )
    reason = forms.CharField(label="السبب", widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        data = super().clean()
        reason = (data.get("reason") or "").strip()
        if data.get("mode") == "write" and len(reason) < 8:
            self.add_error("reason", "وضع التعديل يحتاج سببًا مفصّلًا.")
        return data


class TenantDeleteForm(forms.Form):
    """Typing the slug is the confirmation. Nothing about deleting a client
    should be possible by clicking twice quickly."""

    confirm_slug = forms.CharField(label="اكتب معرّف العميل للتأكيد", max_length=32)
    reason = forms.CharField(label="السبب", widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant

    def clean_confirm_slug(self):
        value = (self.cleaned_data["confirm_slug"] or "").strip().lower()
        if self.tenant and value != self.tenant.slug:
            raise forms.ValidationError("المعرّف لا يطابق.")
        return value
