"""Offline settings for namespace policy tests: never loads production env/services."""
from django.apps import AppConfig


class OfflineUserConfig(AppConfig):
    name = "user"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Production UserConfig starts external MongoDB clients. These tests
        # exercise the models/views against an isolated SQLite database only.
        pass


SECRET_KEY = "offline-namespace-tests"
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "core", "entity", "core.test_settings.OfflineUserConfig", "community", "bot"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
MIGRATION_MODULES = {name: None for name in ("core", "entity", "user", "community", "bot")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

