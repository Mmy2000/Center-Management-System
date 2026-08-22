from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User
from .services import sync_user_group


@receiver(post_save, sender=User)
def sync_role_group(sender, instance, created, **kwargs):
    """Keep auth.Group membership aligned with ``User.role``."""
    if kwargs.get("raw"):  # loaddata
        return
    sync_user_group(instance)


@receiver(user_logged_in)
def record_login_ip(sender, request, user, **kwargs):
    from apps.core.audit import client_ip

    ip = client_ip(request)
    if ip and user.last_login_ip != ip:
        user.last_login_ip = ip
        user.save(update_fields=["last_login_ip"])
