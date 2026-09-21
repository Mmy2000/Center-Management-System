"""Vercel serverless entrypoint.

Vercel imports this module and calls the WSGI callable it exports as `app`;
vercel.json rewrites every path here, so this is the whole of the routing.
"""

import os
import sys
from pathlib import Path

# The function's importable root is this file's directory, not the repository,
# so `cms` and `apps` have to be put on the path before Django is asked for.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms.settings.vercel")

from django.core.wsgi import get_wsgi_application  # noqa: E402

app = get_wsgi_application()
