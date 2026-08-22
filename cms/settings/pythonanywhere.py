"""PythonAnywhere — one self-contained settings file.

This module imports nothing from `base.py` and reads no `.env` and no
environment variables. Everything Django needs is written below, the way a
generated `settings.py` does it, because the PythonAnywhere web worker inherits
nothing from your Bash console — no exports, no virtualenvwrapper hooks — and
every setting that depends on them is a deploy failure waiting to happen.

Point the WSGI file here and reload:

    os.environ["DJANGO_SETTINGS_MODULE"] = "cms.settings.pythonanywhere"

The price of standing alone is that it does not follow `base.py`: if an app
joins INSTALLED_APPS or a middleware is added there, add it here too.

`prod.py` remains the module for the topology in docs/07 §L.1 (PostgreSQL,
Redis, gunicorn behind nginx). This one is the honest subset a free account can
run: SQLite, a per-process cache, static files served by PythonAnywhere itself.
Fine for one center on one screen — see docs/07-deployment.md §L.9.
"""

from pathlib import Path

# =========================================================================== #
# The only two lines you edit
# =========================================================================== #

# Your PythonAnywhere account name: the site is <USERNAME>.pythonanywhere.com.
USERNAME = "Mmy"

# Turn on "Force HTTPS" in the Web tab FIRST, then set this True and reload.
# The other order loops the browser.
FORCE_HTTPS = False


# =========================================================================== #
# Paths & interpreter
# =========================================================================== #

# cms/settings/pythonanywhere.py -> cms/settings -> cms -> <project root>
BASE_DIR = Path(__file__).resolve().parent.parent.parent



# =========================================================================== #
# Core
# =========================================================================== #

DEBUG = False

# This key lives in the repository, so treat it as public: anyone who can read
# the code can forge a session cookie. Replace it after the first deploy —
#   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
# Changing it signs everyone out, nothing more.
SECRET_KEY = "928!bj4y51&zzu^lq3f0*9e2lj+q-fn=8woml97u@8@%jo+ib#"  # noqa: S105

# With DEBUG off, Django answers a blank "Bad Request (400)" to any Host header
# it was not told about — that page is almost always this list and nothing else.
ALLOWED_HOSTS = [f"{USERNAME}.pythonanywhere.com"]

# Django only accepts a cross-origin POST from an origin listed with its scheme,
# and PythonAnywhere serves the site over https. Without this the sign-in form
# comes back "CSRF verification failed".
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS]

ROOT_URLCONF = "cms.urls"
WSGI_APPLICATION = "cms.wsgi.application"
ASGI_APPLICATION = "cms.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# =========================================================================== #
# Applications
# =========================================================================== #

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.accounts",
    "apps.academics",
    "apps.students",
    "apps.cards",
    "apps.lessons",
    "apps.attendance",
    "apps.payments",
    "apps.reports",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AuditContextMiddleware",
    "apps.core.middleware.ForcePasswordChangeMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.branding",
            ],
        },
    },
]


# =========================================================================== #
# Database
# =========================================================================== #
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

# Long report requests must not hold a transaction open: services wrap their own
# writes in transaction.atomic() explicitly.
ATOMIC_REQUESTS = False


# =========================================================================== #
# Authentication
# =========================================================================== #

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 8  # one working day
CSRF_COOKIE_SAMESITE = "Lax"


# =========================================================================== #
# Security
# =========================================================================== #
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


# =========================================================================== #
# Internationalization — Arabic first, RTL, Cairo time (docs/01 §A.3 A6/A7)
# =========================================================================== #

LANGUAGE_CODE = "ar"
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

LANGUAGES = [("ar", "العربية"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]


# =========================================================================== #
# Static & media
# =========================================================================== #
# Map both in the Web tab: /static/ -> staticfiles, /media/ -> media.

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Plain storage, not the hashed manifest: PythonAnywhere maps /static/ straight
# at STATIC_ROOT, and one missing asset in a manifest breaks every page.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


# =========================================================================== #
# Cache & sessions
# =========================================================================== #
# One process, one cache — there is no Redis on the free tier. A setting changed
# in the UI reaches this worker only, which is invisible while there is one.

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cms-default",
    }
}

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"


# =========================================================================== #
# Email
# =========================================================================== #
# Nothing in this project sends email — no password reset, no notifications — so
# the console backend stays and `check --deploy`'s complaint about it is
# silenced deliberately rather than left to fail every deploy.

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}

SILENCED_SYSTEM_CHECKS = ["mail.E001"]


# =========================================================================== #
# Logging — goes to the Web tab's error log
# =========================================================================== #

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        # Never log raw QR tokens here — see docs/06 §I.2.
        "scan": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "audit": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
