"""Seed the editable namespace policy without overwriting operator changes."""
from django.db import migrations

DEFAULT_PATHS = [
    "api", "assets", "conference", "contacts", "delete-account", "design",
    "edits", "explore", "invite", "login", "mapfeed", "messages", "moderation",
    "moments", "notes", "notifications", "pages", "post", "privacy", "realms",
    "register", "scenes", "servers", "settings", "setup", "share", "studio",
    "studios", "support", "terms", "topics", "user", "verification",
]


def seed(apps, schema_editor):
    Variable = apps.get_model("core", "Variable")
    Variable.objects.using(schema_editor.connection.alias).get_or_create(
        key="reserved_profile_paths",
        defaults={
            "value": {"version": 1, "paths": DEFAULT_PATHS},
            "description": 'Reserved profile link names for accounts, realms/pages, and bots. '
                           'Edit the "paths" list; changes apply at the next backend validation. '
                           'This list replaces defaults. Keep application route names reserved.',
        },
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0005_notes_upload_limits")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
