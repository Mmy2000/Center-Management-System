"""PythonAnywhere — including the free tier.

Everything this host needs is written in this file. No `.env`, no environment
variables, no console exports: PythonAnywhere's web worker inherits none of
those anyway, which is what makes them so easy to get wrong. Point the WSGI
file here and reload:

    os.environ["DJANGO_SETTINGS_MODULE"] = "cms.settings.pythonanywhere"

`prod.py` is still the module for the topology in docs/07 §L.1 (PostgreSQL,
Redis, gunicorn behind nginx) and refuses to start without them. This one is
the honest subset a free account can actually run: SQLite, the local-memory
cache, and static files served by PythonAnywhere's own mapping.

Read docs/07-deployment.md §L.9 before going live — the free tier is fine for
one center on one screen, not for a scanning station plus a cashier.
"""

import sys

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import BASE_DIR

# --------------------------------------------------------------------------- #
# The only two lines you edit
# --------------------------------------------------------------------------- #

# Your PythonAnywhere account name: the site is <USERNAME>.pythonanywhere.com.
USERNAME = "Mmy"

# Turn on "Force HTTPS" in the Web tab FIRST, then flip this to True and reload.
# The other order loops the browser.
FORCE_HTTPS = False


# --------------------------------------------------------------------------- #
# Everything below follows from those
# --------------------------------------------------------------------------- #

# Not dead code: the host's virtualenv is whatever Python it was built with,
# and PythonAnywhere still defaults new ones to an older release.
if sys.version_info < (3, 12):  # noqa: UP036
    raise ImproperlyConfigured(
        "This project needs Python 3.12+ (Django 6.1); the virtualenv is "
        f"{sys.version_info.major}.{sys.version_info.minor}. Rebuild it with "
        "mkvirtualenv --python=/usr/bin/python3.13 cms, then set the same "
        "version on the Web tab."
    )

DEBUG = False

# This key lives in the repository, so treat it as public: anyone who can read
# the code can forge a session cookie. Replace it after the first deploy —
# generate one with
#   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
# and paste it here. Changing it signs everyone out, nothing more.
SECRET_KEY = "928!bj4y51&zzu^lq3f0*9e2lj+q-fn=8woml97u@8@%jo+ib#"  # noqa: S105

# With DEBUG off, Django answers a blank "Bad Request (400)" to any Host header
# it was not told about — that page is almost always this list and nothing else.
ALLOWED_HOSTS = [f"{USERNAME}.pythonanywhere.com"]

# Django only accepts a cross-origin POST from an origin listed with its scheme,
# and PythonAnywhere serves the site over https. Without this the sign-in form
# comes back "CSRF verification failed".
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS]


# --------------------------------------------------------------------------- #
# TLS
# --------------------------------------------------------------------------- #
# PythonAnywhere terminates TLS in front of the worker, so the app only ever
# sees http and has to be told to trust the forwarded scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

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
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "CONN_MAX_AGE": 60,
        # A network filesystem plus SQLite means writers can collide; wait
        # instead of failing the request outright.
        "OPTIONS": {"timeout": 20},
    }
}

# One process, one cache — there is no Redis on the free tier. Settings changed
# in the UI reach this worker only, which is invisible while there is one.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cms-default",
    }
}

# Plain storage, not the hashed manifest: PythonAnywhere maps /static/ straight
# at STATIC_ROOT, and one missing asset in a manifest breaks every page.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Goes to the Web tab's error log.
LOGGING["root"]["level"] = "WARNING"  # noqa: F405

# `check --deploy` insists on a real mail backend. Nothing in this project sends
# email — no password reset, no notifications — so the console backend stays and
# the check is silenced deliberately rather than left to fail every deploy.
SILENCED_SYSTEM_CHECKS = ["mail.E001"]
