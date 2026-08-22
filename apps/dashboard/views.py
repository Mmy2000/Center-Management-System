from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """Operational dashboard. Real aggregates land in Phase 10 (TASK-069)."""
    return render(request, "dashboard/home.html", {})
