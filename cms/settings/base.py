"""
Shared Django settings for the Center Management System.

Environment-specific modules (dev / prod / test) import everything from here and
override what differs. See docs/07-deployment.md §L.3.
"""

from pathlib import Path

import environ

# cms/settings/base.py -> cms/settings -> cms -> <project root>
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    SECRET_KEY=(str, ""),
    DATABASE_URL=(str, ""),
    REDIS_URL=(str, ""),
    SENTRY_DSN=(str, ""),
    TIME_ZONE=(str, "Africa/Cairo"),
    LANGUAGE_CODE=(str, "ar"),
    CONSOLE_HOST=(str, ""),
    TENANT_BASE_DOMAIN=(str, "localhost"),
)
# Optional: dev uses it, and cms.settings.pythonanywhere deliberately does not.
# Reading a missing file logs a warning that reads like an error in a host's log.
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")


# --------------------------------------------------------------------------- #
# Applications
# --------------------------------------------------------------------------- #

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

LOCAL_APPS = [
    "apps.core",
    # Must precede every app that will carry a `tenant` FK. `core` stays first:
    # `tenancy.base` extends `core.base.TimeStampedModel`.
    "apps.tenancy",
    "apps.console",
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

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Before AuthenticationMiddleware: the user lookup is itself tenant-scoped.
    # request.user is lazy, so resolving here is early enough (docs/10 §N.3).
    "apps.tenancy.middleware.TenantResolutionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    # After LocaleMiddleware, which activates its own choice and would
    # otherwise overwrite ours (docs/10 §N.3).
    "apps.tenancy.middleware.LanguageMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.tenancy.middleware.TenantSessionGuardMiddleware",
    "apps.tenancy.middleware.ImpersonationGuardMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.tenancy.middleware.TenantStatusMiddleware",
    "apps.core.middleware.AuditContextMiddleware",
    "apps.core.middleware.ForcePasswordChangeMiddleware",
]

# --------------------------------------------------------------------------- #
# Multi-tenancy (docs/10-multi-tenancy.md)
# --------------------------------------------------------------------------- #

# The platform console answers on its own hostname and its own URLconf, so a
# tenant host cannot route to it at all. Empty disables console routing.
CONSOLE_HOST = env("CONSOLE_HOST")
CONSOLE_URLCONF = "cms.urls_console"

# The console's own language. Its templates are Arabic literals, not translation
# calls, so this is pinned rather than negotiated from the browser.
CONSOLE_LANGUAGE = "ar"

# The suffix every client's subdomain hangs off: <slug>.TENANT_BASE_DOMAIN is
# the primary host the provisioning wizard creates (docs/10 §N.11).
TENANT_BASE_DOMAIN = env("TENANT_BASE_DOMAIN")

# Deliberately unset (None): each subdomain then gets its own session cookie, so
# a session simply cannot travel from one center to another. Setting this to
# ".example.com" would share one cookie across every client — the single worst
# bug available in this design (docs/10 §N.6).
SESSION_COOKIE_DOMAIN = None

ROOT_URLCONF = "cms.urls"
WSGI_APPLICATION = "cms.wsgi.application"
ASGI_APPLICATION = "cms.asgi.application"

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
                "apps.core.context_processors.features",
                "apps.core.context_processors.subscription_notice",
            ],
        },
    },
]


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

if env("DATABASE_URL"):
    DATABASES = {"default": env.db("DATABASE_URL")}
else:  # local default; production settings assert Postgres
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DATABASES["default"].setdefault("CONN_MAX_AGE", 60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Long report requests must not hold a transaction open: services wrap their own
# writes in transaction.atomic() explicitly.
ATOMIC_REQUESTS = False


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

AUTH_USER_MODEL = "accounts.User"
# The username is unique per tenant, not globally (docs/10 §N.6), so the login
# lookup has to be scoped before it runs.
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.TenantModelBackend"]
# auth.W004: USERNAME_FIELD is not globally unique. Deliberate — two centers must
# both be able to have an "admin", and telling one of them the name is taken
# would leak the other's existence. Uniqueness is enforced by
# uq_username_per_tenant plus uq_platform_username (docs/10 §N.6).
SILENCED_SYSTEM_CHECKS = ["auth.W004"]
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


# --------------------------------------------------------------------------- #
# Internationalization — Arabic first, RTL, Cairo time (docs/01 §A.3 A6/A7)
# --------------------------------------------------------------------------- #

LANGUAGE_CODE = env("LANGUAGE_CODE")
TIME_ZONE = env("TIME_ZONE")
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

LANGUAGES = [("ar", "العربية"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]


# --------------------------------------------------------------------------- #
# Static & media
# --------------------------------------------------------------------------- #

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

if env("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": env("REDIS_URL"),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cms-default",
        }
    }


# --------------------------------------------------------------------------- #
# Email (Django 6 MAILERS)
# --------------------------------------------------------------------------- #

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # Never log raw QR tokens here — see docs/06 §I.2.
        "scan": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "audit": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"
