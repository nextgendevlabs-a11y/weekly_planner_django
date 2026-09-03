"""ASGI config for the weekly_planner project."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weekly_planner.settings")

application = get_asgi_application()
