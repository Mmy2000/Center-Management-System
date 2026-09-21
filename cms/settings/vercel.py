"""Vercel (Hobby plan) settings — see .env.vercel.example.

Production settings already say what this platform needs; this module only
changes what serverless takes away — a writable disk, and a process that lives
long enough to have been prepared by a build step.
"""

from pathlib import Path

from .prod import *  # noqa: F403
from .prod import DATABASES, MIDDLEWARE

# Every path is routed to the function, so nothing else is listening on
# /static/. WhiteNoise answers there, directly after SecurityMiddleware — early
# enough that a stylesheet never pays for session, tenant and auth work.
MIDDLEWARE = [*MIDDLEWARE]
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

# `collectstatic` writes to STATIC_ROOT, and the disk it would write to is not
# the one the function later reads from. Finders mode serves the files straight
# out of static/ instead, so there is no build step to get wrong. It costs the
# hashed filenames and their year-long cache headers; with 30 files behind
# Vercel's CDN that is a fair trade, and it is why ManifestStaticFilesStorage —
# which raises on every {% static %} without a manifest — is dropped here.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = False
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# /tmp is the only writable path, and it belongs to one instance and survives
# only until that instance is recycled. So student photos uploaded here are
# scratch: they will vanish, and a second instance never sees them at all.
# Keeping the write working rather than raising is the lesser evil while the
# deployment is a demo — before real students are photographed, move this to
# object storage (S3, Cloudinary, Vercel Blob) via STORAGES["default"].
MEDIA_ROOT = Path("/tmp/media")

# Persistent connections assume a process that handles the next request too.
# Here each instance would instead sit holding an idle connection, and a free
# Postgres counts those against a limit measured in tens. The pooled endpoint in
# DATABASE_URL is what makes reconnecting per request cheap.
DATABASES["default"]["CONN_MAX_AGE"] = 0
