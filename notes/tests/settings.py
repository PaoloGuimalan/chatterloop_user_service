"""Database-free unit settings. Never imports project settings or loads .env."""
SECRET_KEY = "notes-unit-tests-only"
INSTALLED_APPS = ["django.contrib.contenttypes", "entity", "notes"]
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}
ROOT_URLCONF = "notes.urls"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
