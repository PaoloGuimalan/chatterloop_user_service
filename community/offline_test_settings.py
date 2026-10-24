"""Offline model/SQL tests: no production settings, network clients or migrations."""

from core.test_settings import *  # noqa: F403
from django.apps import AppConfig


class OfflineNewsfeedConfig(AppConfig):
    name = "newsfeed"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        pass


INSTALLED_APPS = [
    *INSTALLED_APPS,
    "interests",
    "diary",
    "community.offline_test_settings.OfflineNewsfeedConfig",
]  # noqa: F405
REST_FRAMEWORK = {"DEFAULT_AUTHENTICATION_CLASSES": [], "UNAUTHENTICATED_USER": None}
