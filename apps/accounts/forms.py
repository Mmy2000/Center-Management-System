from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.utils.translation import gettext_lazy as _


class CenterAuthenticationForm(AuthenticationForm):
    """One message for every sign-in failure.

    Django says something different for a wrong password, an unknown user
    and a disabled account — enough to tell an attacker which usernames
    exist. Both sign-in paths use this form so both stay quiet.
    """

    error_messages = {
        "invalid_login": _("بيانات دخول غير صحيحة"),
        "inactive": _("بيانات دخول غير صحيحة"),
    }


class BootstrapPasswordChangeForm(PasswordChangeForm):
    """Same rules as Django's form, with Bootstrap-ready widgets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
            field.widget.attrs.setdefault("autocomplete", "new-password")
