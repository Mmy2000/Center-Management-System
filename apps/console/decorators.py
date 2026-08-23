"""Who may reach the console (docs/10 §N.10, TASK-111).

Two separate walls, and both matter:

1. The console lives on its own host with its own URLconf, so a tenant host has
   no route to these views at all. That is structural — no permission bug, no
   path-traversal trick and no proxy misconfiguration can bridge it.
2. Every view still checks ``is_platform_staff``, because a wall you only built
   once is a wall you are trusting more than you should.

Failure is **404**, not 403. A center admin who guesses the console hostname
should learn nothing from the response, including that they guessed right.
"""

from functools import wraps

from django.http import Http404


def _is_platform_staff(request) -> bool:
    if not getattr(request, "is_console", False):
        return False
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_platform_staff)


def platform_staff_required(view):
    """Require a signed-in platform operator on the console host."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not getattr(request, "is_console", False):
            raise Http404("not the console host")
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            from django.contrib.auth.views import redirect_to_login
            from django.urls import reverse

            # Reversed, not hard-coded: under a path-mounted console the login
            # lives at /<prefix>/login/, and a fixed "/login/" would send an
            # operator to the center's sign-in page instead.
            login_url = reverse("console:login", urlconf=request.urlconf)
            return redirect_to_login(request.get_full_path(), login_url)
        if not user.is_platform_staff:
            raise Http404("not platform staff")
        return view(request, *args, **kwargs)

    return wrapper
