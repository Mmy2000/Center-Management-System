"""TASK-093 — the default manager filters, and nothing else has to.

Phase 14 puts twenty real models behind ``TenantOwnedModel``. These two throwaway
models exist so the base class itself is proven *before* any of them depend on
it: the manager, the write guards and the cross-tenant FK check are all tested
against a table this module creates and drops itself.
"""

import pytest
from django.db import connection, models

from apps.tenancy.base import TenantOwnedModel
from apps.tenancy.context import tenant_context
from apps.tenancy.exceptions import CrossTenantWrite, TenantContextRequired

from .factories import make_two_tenants


class Widget(TenantOwnedModel):
    """A minimal tenant-owned model."""

    name = models.CharField(max_length=50)

    class Meta:
        app_label = "tenancy"


class Gadget(TenantOwnedModel):
    """Carries a FK to another tenant-owned model, so the cross-tenant FK guard
    has something to guard. Declares its own Meta on purpose — a subclass must
    not have to remember any manager plumbing."""

    widget = models.ForeignKey(Widget, on_delete=models.CASCADE, related_name="gadgets")
    label = models.CharField(max_length=50)

    class Meta:
        app_label = "tenancy"
        verbose_name = "gadget"


@pytest.fixture(scope="module", autouse=True)
def _tables(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        with connection.schema_editor() as editor:
            editor.create_model(Widget)
            editor.create_model(Gadget)
        yield
        with connection.schema_editor() as editor:
            editor.delete_model(Gadget)
            editor.delete_model(Widget)


pytestmark = pytest.mark.django_db


@pytest.fixture
def pair():
    return make_two_tenants()


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_default_manager_is_filtered(pair):
    alpha, beta = pair
    with tenant_context(alpha):
        Widget.objects.create(name="same-name")
    with tenant_context(beta):
        Widget.objects.create(name="same-name")

    with tenant_context(alpha):
        assert Widget.objects.count() == 1
        assert Widget.objects.get().tenant_id == alpha.pk
    with tenant_context(beta):
        assert Widget.objects.count() == 1
        assert Widget.objects.get().tenant_id == beta.pk


def test_other_tenants_row_is_invisible_by_pk(pair):
    """The sharpest form of the leak: asking for a known primary key."""
    alpha, beta = pair
    with tenant_context(beta):
        theirs = Widget.objects.create(name="theirs")
    with tenant_context(alpha):
        assert not Widget.objects.filter(pk=theirs.pk).exists()
        with pytest.raises(Widget.DoesNotExist):
            Widget.objects.get(pk=theirs.pk)


def test_all_tenants_manager_is_unfiltered(pair):
    alpha, beta = pair
    with tenant_context(alpha):
        Widget.objects.create(name="a")
    with tenant_context(beta):
        Widget.objects.create(name="b")
    assert Widget.all_tenants.count() == 2


def test_no_context_raises_rather_than_returning_nothing(pair):
    """`.none()` would look like a quiet Saturday. A traceback does not."""
    with pytest.raises(TenantContextRequired):
        Widget.objects.count()


def test_default_manager_is_the_filtered_one():
    """`_default_manager` is what admin, ModelChoiceField and get_object_or_404
    reach for — those are exactly the places that must not see other rows."""
    assert Widget._meta.default_manager_name is None
    assert Widget._default_manager.__class__.__name__ == "TenantManager"


def test_base_manager_is_unfiltered(pair):
    """Forward FK traversal and refresh_from_db() go through _base_manager."""
    alpha, _beta = pair
    with tenant_context(alpha):
        widget = Widget.objects.create(name="w")
    # Outside any context: the base manager must still work.
    assert Widget._base_manager.filter(pk=widget.pk).exists()


def test_forward_fk_traversal_works_without_context(pair):
    alpha, _beta = pair
    with tenant_context(alpha):
        widget = Widget.objects.create(name="w")
        gadget = Gadget.objects.create(widget=widget, label="g")
    fresh = Gadget.all_tenants.get(pk=gadget.pk)
    assert fresh.widget.name == "w"  # no tenant in context — must not raise


def test_refresh_from_db_works_without_context(pair):
    alpha, _beta = pair
    with tenant_context(alpha):
        widget = Widget.objects.create(name="w")
    widget.refresh_from_db()
    assert widget.name == "w"


def test_reverse_related_manager_is_scoped(pair):
    alpha, _beta = pair
    with tenant_context(alpha):
        widget = Widget.objects.create(name="w")
        Gadget.objects.create(widget=widget, label="g1")
        Gadget.objects.create(widget=widget, label="g2")
        assert widget.gadgets.count() == 2


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


def test_save_stamps_the_tenant_from_context(pair):
    alpha, _beta = pair
    with tenant_context(alpha):
        widget = Widget(name="w")
        assert widget.tenant_id is None
        widget.save()
    assert widget.tenant_id == alpha.pk


def test_save_with_no_tenant_anywhere_raises(pair):
    with pytest.raises(TenantContextRequired):
        Widget(name="w").save()


def test_save_refuses_a_tenant_mismatch(pair):
    alpha, beta = pair
    with tenant_context(alpha):
        with pytest.raises(CrossTenantWrite):
            Widget(name="w", tenant=beta).save()


def test_cross_tenant_fk_is_rejected(pair):
    """The database cannot express this without composite FKs, so the model does."""
    alpha, beta = pair
    with tenant_context(beta):
        theirs = Widget.objects.create(name="theirs")
    with tenant_context(alpha):
        with pytest.raises(CrossTenantWrite):
            Gadget.objects.create(widget=theirs, label="smuggled")


def test_same_tenant_fk_is_fine(pair):
    alpha, _beta = pair
    with tenant_context(alpha):
        widget = Widget.objects.create(name="ours")
        gadget = Gadget.objects.create(widget=widget, label="fine")
    assert gadget.tenant_id == alpha.pk


def test_full_clean_also_rejects_a_cross_tenant_fk(pair):
    alpha, beta = pair
    with tenant_context(beta):
        theirs = Widget.objects.create(name="theirs")
    with tenant_context(alpha):
        gadget = Gadget(widget=theirs, label="x", tenant=alpha)
        with pytest.raises(CrossTenantWrite):
            gadget.clean()


def test_bulk_create_stamps_every_row(pair):
    """bulk_create bypasses save(), so the stamp has to happen in the queryset —
    the card importer and the lesson generator both go through it."""
    alpha, _beta = pair
    with tenant_context(alpha):
        Widget.objects.bulk_create([Widget(name=f"w{i}") for i in range(50)])
        assert Widget.objects.count() == 50
    assert Widget.all_tenants.filter(tenant=alpha).count() == 50


def test_bulk_create_without_context_raises(pair):
    with pytest.raises(TenantContextRequired):
        Widget.objects.bulk_create([Widget(name="w")])


def test_bulk_create_refuses_a_foreign_row(pair):
    alpha, beta = pair
    with tenant_context(alpha):
        with pytest.raises(CrossTenantWrite):
            Widget.objects.bulk_create([Widget(name="w", tenant=beta)])


def test_tenant_field_is_not_editable():
    """It must never appear in a ModelForm, where a POST could set it."""
    assert Widget._meta.get_field("tenant").editable is False


def test_update_does_not_bypass_the_filter(pair):
    """`.update()` cannot be guarded by save(), but it is still filtered."""
    alpha, beta = pair
    with tenant_context(beta):
        theirs = Widget.objects.create(name="theirs")
    with tenant_context(alpha):
        Widget.objects.update(name="rewritten")
    theirs.refresh_from_db()
    assert theirs.name == "theirs"


def test_delete_does_not_reach_across(pair):
    alpha, beta = pair
    with tenant_context(beta):
        Widget.objects.create(name="theirs")
    with tenant_context(alpha):
        Widget.objects.all().delete()
    assert Widget.all_tenants.count() == 1
