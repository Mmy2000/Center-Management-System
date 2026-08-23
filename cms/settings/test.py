"""Test settings — fast hashers, in-memory cache, deterministic behaviour."""

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-only-key"  # noqa: S105
# `.testserver` mirrors production's `.yourapp.com`: multi-tenant tests need
# several hosts resolving to one app, and the tenancy middleware — not this
# list — is what decides which of them is a real center (docs/10 §N.11).
# `.example.com` stands in for a client's own custom domain.
ALLOWED_HOSTS = [
    "testserver",
    ".testserver",
    "localhost",
    "127.0.0.1",
    ".example.com",
]

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cms-test",
    }
}

# Keep test output quiet unless something breaks.
LOGGING["root"]["level"] = "ERROR"  # noqa: F405
