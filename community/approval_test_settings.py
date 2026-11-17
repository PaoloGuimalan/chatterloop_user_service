"""Isolated review/role tests; never imports production settings or clients."""

from community.offline_test_settings import *  # noqa: F403

MIGRATION_MODULES = {app.rsplit(".", 1)[-1]: None for app in (
    "core", "entity", "user", "community", "bot", "interests", "diary", "newsfeed",
)}
ROOT_URLCONF = __name__
urlpatterns = []
REALM_ROLE_CONFIGURATION_ENABLED = True
COMMUNITY_APPROVALS_ENABLED = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
