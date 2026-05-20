"""WSGI config for Postino."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cli.settings")

application = get_wsgi_application()
