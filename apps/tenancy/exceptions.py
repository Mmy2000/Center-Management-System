"""Tenancy failures that must never be swallowed (docs/10 §N.4).

Every one of these is a programming error, not a user error: they mean a query
or a write reached the ORM without knowing which center it belongs to. They are
deliberately *not* subclasses of ``DomainError`` — nothing should catch them and
turn them into a polite JSON envelope.
"""


class TenancyError(Exception):
    """Base for every tenancy invariant violation."""


class TenantContextRequired(TenancyError):
    """A tenant-scoped query ran with no tenant in context.

    Returning an empty queryset here would be worse than raising: a report that
    silently shows zero students looks like a quiet Saturday, not like a bug.
    """


class CrossTenantWrite(TenancyError):
    """A write would have landed in, or pointed at, another center's data."""


class UnknownFeature(TenancyError, KeyError):
    """A feature key that is not in ``features.FEATURES`` (typo protection).

    Subclasses ``KeyError`` so it reads naturally at the call site and so the
    contract matches ``policies.spec_for``, which raises ``KeyError`` for an
    unknown settings key.
    """
