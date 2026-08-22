from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Setting
from .registry import settings_registry


@receiver(post_save, sender=Setting)
@receiver(post_delete, sender=Setting)
def invalidate_settings_cache(sender, instance, **kwargs):
    settings_registry.invalidate()
