from django.shortcuts import get_object_or_404, render

from apps.accounts.decorators import require_perm
from apps.accounts.scoping import visible_groups

from .models import Group, GroupStatus


@require_perm("academics.view_educationalstage")
def structure_page(request):
    """Stages / grades / subjects / offerings / instructors, one page, five tabs."""
    return render(request, "academics/structure.html", {})


@require_perm("academics.view_group")
def groups_page(request):
    return render(
        request,
        "academics/groups.html",
        {"statuses": GroupStatus.choices},
    )


@require_perm("academics.view_group")
def group_detail_page(request, pk):
    group = get_object_or_404(
        visible_groups(
            request.user,
            Group.objects.select_related(
                "grade_subject__subject", "grade_subject__grade__stage", "instructor"
            ),
        ),
        pk=pk,
    )
    return render(
        request,
        "academics/group_detail.html",
        {"group": group, "schedules": group.schedules.all()},
    )
