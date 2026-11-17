"""Explicit disposable PostgreSQL only; no fallback to application credentials."""

import os

from community.approval_test_settings import *  # noqa: F403

_prefix = "COMMUNITY_APPROVAL_TEST_DB_"
_required = ("NAME", "HOST", "PORT", "USER", "PASSWORD")
if any(not os.environ.get(_prefix + key) for key in _required):
    raise RuntimeError("Supply an explicit disposable community approval test database.")
_database = os.environ[_prefix + "NAME"]
if not _database.startswith("test_"):
    raise RuntimeError("Community approval tests require a database named test_*.")
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    **{key: os.environ[_prefix + key] for key in _required},
    "TEST": {"NAME": _database + "_run"},
}}
