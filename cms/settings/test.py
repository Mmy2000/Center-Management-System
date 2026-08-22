"""Test settings — fast hashers, in-memory cache, deterministic behaviour."""

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-only-key"  # noqa: S105
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cms-test",
    }
}

# Keep test output quiet unless something breaks.
LOGGING["root"]["level"] = "ERROR"  # noqa: F405
