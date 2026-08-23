"""Local development settings."""

from .base import *  # noqa: F403
from .base import BASE_DIR, env  # noqa: F401

DEBUG = True

# `.localhost` covers the console and every client subdomain — browsers resolve
# *.localhost to 127.0.0.1 on their own (RFC 6761), so multi-tenant routing
# works locally with no hosts-file editing. This mirrors production's
# `.yourapp.com`; the tenancy middleware, not this list, decides which of those
# hostnames is a real center (docs/10 §N.11).
ALLOWED_HOSTS = ["localhost", ".localhost", "127.0.0.1", "[::1]", "testserver"]
INTERNAL_IPS = ["127.0.0.1"]

# Sensible local defaults so `runserver` works with no .env at all. An explicit
# env var still wins.
if not env("CONSOLE_HOST", default=""):
    CONSOLE_HOST = "console.localhost"
if not env("TENANT_BASE_DOMAIN", default=""):
    TENANT_BASE_DOMAIN = "localhost"

if not env("SECRET_KEY", default=""):
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"  # noqa: S105

# Console mail, no SSL redirects, permissive cookies over plain HTTP.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
