import re

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DefaultsOptionalModelForm

from .models import Student

EGYPT_MOBILE = re.compile(r"^01[0125]\d{8}$")
MAX_PHOTO_BYTES = 2 * 1024 * 1024


PHONE_LABEL = _("رقم الهاتف")


def _clean_phone(value: str, *, required=False, label=None) -> str:
    label = label or PHONE_LABEL
    value = (value or "").strip().replace(" ", "").replace("-", "")
    if not value:
        if required:
            raise forms.ValidationError(_("%(label)s مطلوب") % {"label": label})
        return ""
    if value.startswith("+20"):
        value = "0" + value[3:]
    if not EGYPT_MOBILE.match(value):
        raise forms.ValidationError(_("رقم موبايل مصري غير صحيح (مثال: 01012345678)"))
    return value


class StudentForm(DefaultsOptionalModelForm):
    class Meta:
        model = Student
        fields = [
            "full_name",
            "photo",
            "date_of_birth",
            "gender",
            "phone",
            "guardian_name",
            "guardian_phone",
            "guardian_relation",
            "address",
            "grade",
            "school",
            "status",
            "enrolled_on",
            "notes",
        ]

    def clean_full_name(self):
        name = " ".join((self.cleaned_data.get("full_name") or "").split())
        if len(name) < 3:
            raise forms.ValidationError(_("الاسم قصير جدًا"))
        return name

    def clean_phone(self):
        return _clean_phone(self.cleaned_data.get("phone"))

    def clean_guardian_phone(self):
        # Required in practice (it is how the center reaches the family), but
        # enforced by the form rather than the database so imports stay possible.
        return _clean_phone(
            self.cleaned_data.get("guardian_phone"),
            required=True,
            label=_("هاتف ولي الأمر"),
        )

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and getattr(photo, "size", 0) > MAX_PHOTO_BYTES:
            raise forms.ValidationError(_("حجم الصورة يجب ألا يتجاوز 2 ميجابايت"))
        return photo
