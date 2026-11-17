"""Migration/SQL contracts on an explicitly supplied disposable PostgreSQL DB."""
from entity.test_settings import *  # noqa: F403
from community.approval_postgres_test_settings import DATABASES

DATABASES = {"default": {**DATABASES["default"], "TEST": {
    "NAME": DATABASES["default"]["NAME"] + "_migration_run",
}}}
