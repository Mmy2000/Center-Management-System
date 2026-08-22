from django.contrib.auth.models import Group

from .permissions import GROUP_NAMES


def sync_user_group(user) -> None:
    """Make the user's auth.Group membership match their ``role``.

    Role is the convenience column; the Group carries the permissions actually
    checked at runtime.
    """
    target_name = GROUP_NAMES.get(user.role)
    role_groups = list(Group.objects.filter(name__in=GROUP_NAMES.values()))
    current = {g.name for g in user.groups.all()}

    for group in role_groups:
        if group.name == target_name and group.name not in current:
            user.groups.add(group)
        elif group.name != target_name and group.name in current:
            user.groups.remove(group)
