"""Permission helpers for page views (AJAX views use ``@ajax(perm=...)``)."""

from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


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
