"""Permission/model migration tests against SQLite, without production clients."""

from core.test_settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "notes"]  # noqa: F405
MIGRATION_MODULES = {}
