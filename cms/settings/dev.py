"""Local development settings."""

from .base import *  # noqa: F403
from .base import BASE_DIR, env  # noqa: F401

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]
INTERNAL_IPS = ["127.0.0.1"]

if not env("SECRET_KEY", default=""):
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"  # noqa: S105

# Console mail, no SSL redirects, permissive cookies over plain HTTP.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
