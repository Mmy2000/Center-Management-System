"""Authentication inside one center (docs/10 §N.6, TASK-094).

``ModelBackend`` resolves a login with ``get_by_natural_key(username)``. Once a
username is unique only *per tenant*, that call can match several rows, so the
lookup has to be scoped before it runs — not filtered afterwards, which would
already have leaked which centers hold that name.

The manager (``accounts.managers``) does the scoping, so this backend is thin:
it exists to make the intent explicit and to keep the timing-attack defence that
``ModelBackend`` performs when no user matches.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class TenantModelBackend(ModelBackend):
    """Authenticate against the current tenant, or the platform outside one.

    Also refuses a user whose tenant is no longer operational, so a suspended
    center cannot be entered even if the status middleware is somehow bypassed.
    """

    def get_user(self, user_id):
        """Load the user *and* their tenant in one query.

        ``AuthenticationMiddleware`` calls this on every authenticated request,
        and ``user_can_authenticate`` below reads ``user.tenant`` — without the
        join that would be a second query on every page in the system.
        """
        UserModel = get_user_model()
        try:
            user = UserModel._default_manager.select_related("tenant").get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False
        tenant = getattr(user, "tenant", None)
        if tenant is not None and not tenant.is_operational:
            return False
        return True
