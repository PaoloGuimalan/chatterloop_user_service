"""
Seeds the upload settings. Only creates rows that are missing, so re-running
it (or running it after someone already tuned a value in the admin) never
overwrites anything. The same defaults are hardcoded in the Node server
(server/reusables/media/config.js) as its fallback.
"""

from django.db import migrations

UPLOAD_LIMITS = {
    "message": {"maxMB": 100, "types": ["*"]},
    "voice_note": {"maxMB": 25, "types": ["audio/*"]},
    "post_media": {"maxMB": 100, "types": ["image/*", "video/*"]},
    "moment": {"maxMB": 100, "types": ["image/*", "video/mp4"]},
    "moment_poster": {"maxMB": 10, "types": ["image/jpeg", "image/png"]},
    "diary": {"maxMB": 100, "types": ["*"]},
    "avatar": {"maxMB": 10, "types": ["image/*"]},
    "cover": {"maxMB": 10, "types": ["image/*"]},
    "comment": {"maxMB": 10, "types": ["image/*"]},
}

UPLOAD_TRANSFER = {"multipartThresholdMB": 16, "partSizeMB": 8, "concurrency": 4}

ROWS = [
    (
        "upload_limits",
        UPLOAD_LIMITS,
        "Per upload feature: maxMB (file size cap) and types (allowed MIME "
        "patterns, '*' = anything). Clients fetch this on boot.",
    ),
    (
        "upload_transfer",
        UPLOAD_TRANSFER,
        "Files of at least multipartThresholdMB go up in partSizeMB parts, "
        "concurrency at a time. partSizeMB must be at least 5 (storage minimum).",
    ),
]


def seed(apps, schema_editor):
    Variable = apps.get_model("core", "Variable")
    for key, value, description in ROWS:
        Variable.objects.get_or_create(
            key=key, defaults={"value": value, "description": description}
        )


def unseed(apps, schema_editor):
    Variable = apps.get_model("core", "Variable")
    Variable.objects.filter(key__in=[key for key, _, _ in ROWS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_variable"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
