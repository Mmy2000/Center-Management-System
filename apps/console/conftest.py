"""The console's tests build their own tenants, like tenancy's do."""

import pytest


@pytest.fixture(autouse=True)
def default_tenant():
    yield None
