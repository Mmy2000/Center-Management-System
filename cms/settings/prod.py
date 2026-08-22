"""Production settings — see docs/07-deployment.md."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import DATABASES, env

DEBUG = False

if not env("SECRET_KEY", default=""):
    raise ImproperlyConfigured("SECRET_KEY must be set in production.")
if env("DEBUG"):
    raise ImproperlyConfigured("DEBUG must be False in production.")
if not env("ALLOWED_HOSTS"):
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")
if "postgresql" not in DATABASES["default"].get("ENGINE", ""):
    raise ImproperlyConfigured(
        "Production requires PostgreSQL (row locking + partial indexes). "
        "Set DATABASE_URL=postgres://..."
    )

# TLS / proxy
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Cookies
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Headers
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Sessions in Redis when available (falls back to the DB backend).
if env("REDIS_URL"):
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    SESSION_CACHE_ALIAS = "default"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}

LOGGING["root"]["level"] = "WARNING"  # noqa: F405
