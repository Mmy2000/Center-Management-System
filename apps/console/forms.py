"""Console forms (TASK-113/114/116)."""

from django import forms
from django.conf import settings

from apps.tenancy.constants import BillingCycle, FeatureState
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
    # Only plans that are still on offer. A retired plan keeps its existing
    # clients but must not be handed to a new one.
    plan = forms.ModelChoiceField(label="الباقة", queryset=Plan.objects.filter(is_active=True))
    billing_cycle = forms.ChoiceField(
        label="دورة الفوترة", choices=BillingCycle.choices, initial=BillingCycle.MONTHLY
    )
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


class PlanForm(forms.ModelForm):
    """Create or edit a plan, including what it costs.

    The features a plan grants are edited on the same screen but stored in a
    separate table, so they arrive as a checkbox list rather than a model field.
    """

    features = forms.MultipleChoiceField(
        label="الخصائص المشمولة",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Plan
        fields = [
            "name",
            "slug",
            "description",
            "is_active",
            "is_public",
            "sort_order",
            "monthly_price",
            "yearly_price",
            "currency",
            "discount_percent",
            "discount_label",
            "discount_until",
            "max_students",
            "max_users",
            "max_groups",
            "max_cards",
            "storage_mb",
            "retention_days",
            "price_note",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.tenancy.features import FEATURES

        self.fields["features"].choices = [
            (spec.key, f"{spec.label} ({spec.key})") for spec in FEATURES
        ]
        if self.instance.pk:
            self.fields["features"].initial = sorted(self.instance.feature_keys)
            # The slug is what `seed_plans` and any script keys off; renaming it
            # would orphan them silently.
            self.fields["slug"].disabled = True
        else:
            from apps.tenancy.features import DEFAULT_ON_KEYS

            self.fields["features"].initial = sorted(DEFAULT_ON_KEYS)

    def clean(self):
        data = super().clean()

        monthly = data.get("monthly_price")
        yearly = data.get("yearly_price")
        if monthly is None and yearly is None and data.get("is_active"):
            # Not an error — a free or internal plan is legitimate — but say so
            # once rather than leaving an operator wondering later.
            self.add_error(None, "الباقة بلا سعر شهري ولا سنوي: ستظهر كباقة مجانية.")

        if data.get("discount_percent") and not data.get("discount_label"):
            self.add_error("discount_label", "اكتب سبب الخصم حتى يُفهم لاحقًا.")

        selected = set(data.get("features") or [])
        from apps.tenancy.features import CORE_KEYS, IMPLEMENTED_METHOD_KEYS

        # A plan that grants no way of taking attendance would produce a center
        # that cannot record anything the moment it is assigned.
        if not (selected & IMPLEMENTED_METHOD_KEYS):
            self.add_error(
                "features", "اختر طريقة واحدة على الأقل لتسجيل الحضور (البطاقة أو اليدوي)."
            )

        # Core keys are always on regardless; adding them silently keeps the
        # stored plan honest about what it actually grants.
        data["features"] = sorted(selected | set(CORE_KEYS))
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
    plan = forms.ModelChoiceField(label="الباقة", queryset=Plan.objects.filter(is_active=True))
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
