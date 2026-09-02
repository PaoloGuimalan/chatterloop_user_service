# Seeds the capabilities the developer API gates on.
#
# Global-scoped, and deliberately given NO entry in GLOBAL_PLATFORM_DEFAULT
# (entity/permissions.py): has_permission() falls through to `return False`
# when a global permission has no default predicate, so both are
# DENY-BY-DEFAULT and need an explicit EntityPermission grant on whichever
# entity is to hold them.
#
# That asymmetry is on purpose. Every other global permission defaults to
# _account_in_good_standing, whose `getattr(entity, "users", None)` returns
# None for any entity with no Account - realms and bots alike, because Django's
# reverse one-to-one raises an AttributeError subclass - and therefore returns
# True. Seeding these the same way would have granted them to every bot that
# exists, which is the opposite of the point.
#
# Literal codenames, not imports from entity/permissions.py, matching 0004:
# a migration must not depend on application code that can change shape later.

from django.db import migrations

DEVELOPER_API_PERMISSIONS = [
    (
        "messages.read",
        "Read conversation history through the developer API. Does not grant "
        "membership - the caller must still be a participant.",
    ),
    (
        "notifications.read",
        "Read the acting entity's own notifications through the developer API, "
        "including the comment mentions addressed to it.",
    ),
    (
        "events.subscribe",
        "Subscribe to the acting entity's realtime event stream over SSE.",
    ),
]


def seed(apps, schema_editor):
    PermissionCatalogEntry = apps.get_model("entity", "PermissionCatalogEntry")
    for codename, description in DEVELOPER_API_PERMISSIONS:
        PermissionCatalogEntry.objects.update_or_create(
            codename=codename,
            defaults={"scope": "global", "description": description, "is_active": True},
        )


def unseed(apps, schema_editor):
    PermissionCatalogEntry = apps.get_model("entity", "PermissionCatalogEntry")
    PermissionCatalogEntry.objects.filter(
        codename__in=[c for c, _ in DEVELOPER_API_PERMISSIONS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0015_token"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
