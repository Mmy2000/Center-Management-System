"""Tenancy's own tests build their tenants themselves.

The project-level ``default_tenant`` fixture exists so that the pre-tenancy
tests keep working. Here it would be in the way: these tests count tenants,
switch between two of them, and assert what happens with *no* tenant at all.
"""

import pytest


@pytest.fixture(autouse=True)
def default_tenant():
    yield None
