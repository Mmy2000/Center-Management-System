"""Access helpers for page views (AJAX views use ``@ajax(perm=..., feature=...)``).

Two orthogonal gates, and both must pass:

    permission  what this *user* may do        — identical in every center
    feature     what this *center* bought      — differs per client

They are never conflated. The permission matrix in
:mod:`apps.accounts.permissions` stays the same everywhere, and a per-client
difference is expressed as a feature, never as a stripped permission.
"""

from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404


def require_perm(*perms, any_of=False):
    """Require permissions on a page view.

    Anonymous users are sent to the login page; authenticated users without the
    permission get a 403.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            check = any if any_of else all
            if not check(user.has_perm(p) for p in perms):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class RequirePermMixin(AccessMixin):
    """Class-based-view counterpart of :func:`require_perm`."""

    required_permissions: tuple[str, ...] = ()
    require_any = False

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return self.handle_no_permission()
        check = any if self.require_any else all
        if self.required_permissions and not check(
            user.has_perm(p) for p in self.required_permissions
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def require_feature(*feature_keys):
    """Require every named feature on a page view.

    Raises ``Http404``, deliberately — not ``PermissionDenied``. A 403 tells a
    center that did not buy payments that the payments page exists and is being
    withheld; a 404 tells them nothing, which is the honest answer to "does this
    URL exist for me".
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            from apps.tenancy.resolver import has_feature

            for key in feature_keys:
                if not has_feature(key):
                    raise Http404(f"feature disabled: {key}")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class RequireFeatureMixin:
    """Class-based-view counterpart of :func:`require_feature`."""

    required_features: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        from apps.tenancy.resolver import has_feature

        for key in self.required_features:
            if not has_feature(key):
                raise Http404(f"feature disabled: {key}")
        return super().dispatch(request, *args, **kwargs)
