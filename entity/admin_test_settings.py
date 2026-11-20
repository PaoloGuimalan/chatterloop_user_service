"""Run real admin discovery/checks with isolated SQLite and inert service clients."""

from entity.test_settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS,  # noqa: F405
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.admin']
MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
ROOT_URLCONF = __name__
urlpatterns = []
