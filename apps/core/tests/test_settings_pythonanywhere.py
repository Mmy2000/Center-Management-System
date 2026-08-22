"""cms.settings.pythonanywhere stands alone — so it can silently fall behind.

It deliberately copies base.py instead of importing it (docs/07 §L.9), which
means an app added to INSTALLED_APPS, or a middleware, or a context processor,
lands in one file and not the other. These tests are the only thing that
notices.
"""

import ast
import importlib

import pytest

base = importlib.import_module("cms.settings.base")
pa = importlib.import_module("cms.settings.pythonanywhere")

SHARED = [
    "INSTALLED_APPS",
    "MIDDLEWARE",
    "TEMPLATES",
    "ROOT_URLCONF",
    "WSGI_APPLICATION",
    "ASGI_APPLICATION",
    "DEFAULT_AUTO_FIELD",
    "ATOMIC_REQUESTS",
    "AUTH_USER_MODEL",
    "LOGIN_URL",
    "LOGIN_REDIRECT_URL",
    "LOGOUT_REDIRECT_URL",
    "AUTH_PASSWORD_VALIDATORS",
    "SESSION_COOKIE_HTTPONLY",
    "SESSION_COOKIE_SAMESITE",
    "SESSION_COOKIE_AGE",
    "CSRF_COOKIE_SAMESITE",
    "LANGUAGE_CODE",
    "TIME_ZONE",
    "USE_I18N",
    "USE_TZ",
    "USE_THOUSAND_SEPARATOR",
    "LANGUAGES",
    "LOCALE_PATHS",
    "STATIC_URL",
    "STATIC_ROOT",
    "STATICFILES_DIRS",
    "MEDIA_URL",
    "MEDIA_ROOT",
    "DATA_UPLOAD_MAX_MEMORY_SIZE",
    "FILE_UPLOAD_MAX_MEMORY_SIZE",
    "MAILERS",
    "MESSAGE_STORAGE",
    "BASE_DIR",
]


@pytest.mark.parametrize("setting", SHARED)
def test_shared_setting_matches_base(setting):
    assert getattr(pa, setting) == getattr(base, setting), (
        f"{setting} drifted: copy the change from cms/settings/base.py into "
        "cms/settings/pythonanywhere.py"
    )


def test_it_really_is_standalone():
    """No import from base, no django-environ, no os.environ.

    Read the AST, not the text: the module docstring quotes the WSGI file, and
    that quote contains both `os.environ` and the word base.
    """
    source = (pa.BASE_DIR / "cms" / "settings" / "pythonanywhere.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))

    assert not {"os", "environ"} & imported
    assert not any(name.endswith("base") for name in imported)


def test_the_host_is_derived_from_one_name():
    """One typo cannot make ALLOWED_HOSTS and the CSRF origin disagree."""
    assert pa.ALLOWED_HOSTS == [f"{pa.USERNAME}.pythonanywhere.com"]
    assert pa.CSRF_TRUSTED_ORIGINS == [f"https://{pa.USERNAME}.pythonanywhere.com"]


def test_https_settings_follow_one_flag():
    for setting in ("SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"):
        assert getattr(pa, setting) is pa.FORCE_HTTPS


def test_it_is_a_production_configuration():
    assert pa.DEBUG is False
    assert pa.SECRET_KEY
    # MySQL ignores the partial unique indexes this schema depends on.
    assert pa.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    # The hashed manifest breaks every page over one missing asset.
    assert pa.STORAGES["staticfiles"]["BACKEND"].endswith("StaticFilesStorage")
