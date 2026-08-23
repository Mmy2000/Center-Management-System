import json

from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from apps.core import ratelimit
from apps.core.audit import client_ip

from .auth_api import LOGIN_LIMIT, LOGIN_WINDOW  # one throttle for both paths
from .decorators import require_feature, require_perm
from .forms import BootstrapPasswordChangeForm, CenterAuthenticationForm
from .models import Role


class CenterLoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = CenterAuthenticationForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        identity = client_ip(request) or "unknown"
        if not ratelimit.hit("login", identity, limit=LOGIN_LIMIT, window=LOGIN_WINDOW):
            form = self.get_form()
            form.add_error(None, _("عدد محاولات كبير من هذا الجهاز. برجاء المحاولة بعد دقيقة."))
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)


class CenterLogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("accounts:login")


class CenterPasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = BootstrapPasswordChangeForm
    success_url = reverse_lazy("dashboard:home")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
        return response


@login_required
def profile(request):
    return render(request, "accounts/profile.html", {"user_obj": request.user})


@require_perm("accounts.view_user")
@require_feature("users.management")
def users_page(request):
    """Staff account management (TASK-010)."""
    roles = [[value, str(label)] for value, label in Role.choices]
    return render(
        request,
        "accounts/users.html",
        {"roles_json": json.dumps(roles, ensure_ascii=False)},
    )
