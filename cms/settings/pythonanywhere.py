"""PythonAnywhere — including the free tier.

`prod.py` targets the docker topology in docs/07 (PostgreSQL, Redis, gunicorn
behind nginx) and refuses to start without them. A free PythonAnywhere account
has none of those, so this module is the small, honest subset that runs there:
SQLite, the local-memory cache, and static files served by PythonAnywhere's own
mapping instead of nginx.

Point the WSGI file at it:

    os.environ["DJANGO_SETTINGS_MODULE"] = "cms.settings.pythonanywhere"

Read docs/07-deployment.md §L.9 before going live — the free tier is fine for
one center on one screen, not for a scanning station plus a cashier.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import BASE_DIR, env

DEBUG = False

# --------------------------------------------------------------------------- #
# Hosts
# --------------------------------------------------------------------------- #
# With DEBUG off, Django answers 400 Bad Request to any host that is not listed
# here — that empty "Bad Request (400)" page is almost always this and nothing
# else. ALLOWED_HOSTS from .env wins; otherwise we derive <user>.pythonanywhere.com
# from the variables PythonAnywhere sets for the web worker.

_user = env("USER", default="") or env("PYTHONANYWHERE_USER", default="")
_domain = env("PYTHONANYWHERE_DOMAIN", default="pythonanywhere.com")

ALLOWED_HOSTS = env("ALLOWED_HOSTS") or ([f"{_user}.{_domain}"] if _user else [])
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "Set ALLOWED_HOSTS in .env, e.g. ALLOWED_HOSTS=myname.pythonanywhere.com"
    )

# Django only accepts a cross-origin POST from an origin listed with its scheme,
# and PythonAnywhere serves the site over https. Derive it so the sign-in form
# is not rejected with "CSRF verification failed".
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS") or [
    f"https://{host}" for host in ALLOWED_HOSTS if not host.startswith(".")
]

# --------------------------------------------------------------------------- #
# TLS
# --------------------------------------------------------------------------- #
# PythonAnywhere terminates TLS in front of the worker, so the app only ever
# sees http and has to be told to trust the forwarded scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Off by default: turn on "Force HTTPS" in the Web tab first, then set
# FORCE_HTTPS=True. Doing it the other way round loops the browser.
FORCE_HTTPS = env.bool("FORCE_HTTPS", default=False)
SECURE_SSL_REDIRECT = FORCE_HTTPS
SESSION_COOKIE_SECURE = FORCE_HTTPS
CSRF_COOKIE_SECURE = FORCE_HTTPS

# HSTS is a promise the browser remembers for a year. Not on a free subdomain.
SECURE_HSTS_SECONDS = 0

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
# SQLite, deliberately: the schema uses partial unique indexes (one active card
# per student, one attendance per student per lesson) which MySQL silently
# ignores — the free tier's MySQL would let duplicates through.
if not env("DATABASE_URL"):
    DATABASES["default"]["NAME"] = BASE_DIR / "db.sqlite3"  # noqa: F405
    # A network filesystem plus SQLite means writers can collide; wait instead
    # of failing the request outright.
    DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = 20  # noqa: F405

# Plain storage, not the hashed manifest: PythonAnywhere maps /static/ straight
# at STATIC_ROOT, and one missing asset in a manifest breaks every page.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOGGING["root"]["level"] = "WARNING"  # noqa: F405
