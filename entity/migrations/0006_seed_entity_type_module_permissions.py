from django.db import migrations

# Inline literal seed data, not imported from entity/permissions.py -
# migrations must never depend on application code that can change shape
# later. Frozen, correct-at-time-of-writing snapshot matching
# entity/permissions.py's Permission.ENTITY_TYPE_SCOPED constants.

ENTITY_TYPE_SCOPED_PERMISSIONS = [
    "module.diary.access",
    "module.poke.access",
    "module.contacts.access",
    "module.page_dashboard.access",
    "module.page_members.access",
    "module.page_invites.access",
]

ENTITY_TYPE_DEFAULT_PERMISSIONS = {
    # Diary is the one module tied to Account rather than Entity (a page has
    # no diary of its own - showing it while acting as a page would just be
    # your personal diary peeking through), so it stays user-only. Poke and
    # Contacts are universal - both work identically regardless of which
    # entity is acting, since entity_id is what Connection/notifications key
    # off of either way.
    "user": [
        "module.diary.access",
        "module.poke.access",
        "module.contacts.access",
    ],
    "realm": [
        "module.poke.access",
        "module.contacts.access",
        "module.page_dashboard.access",
        "module.page_members.access",
        "module.page_invites.access",
    ],
    "bot": [],
}


def seed_entity_type_permissions(apps, schema_editor):
    PermissionCatalogEntry = apps.get_model("entity", "PermissionCatalogEntry")
    EntityTypeDefaultPermission = apps.get_model("entity", "EntityTypeDefaultPermission")

    entries_by_codename = {}
    for codename in ENTITY_TYPE_SCOPED_PERMISSIONS:
        entries_by_codename[codename] = PermissionCatalogEntry.objects.create(
            codename=codename, scope="entity_type"
        )

    for entity_type, codenames in ENTITY_TYPE_DEFAULT_PERMISSIONS.items():
        for codename in codenames:
            EntityTypeDefaultPermission.objects.create(
                entity_type=entity_type, permission=entries_by_codename[codename]
            )


def unseed_entity_type_permissions(apps, schema_editor):
    PermissionCatalogEntry = apps.get_model("entity", "PermissionCatalogEntry")
    PermissionCatalogEntry.objects.filter(
        codename__in=ENTITY_TYPE_SCOPED_PERMISSIONS
    ).delete()
    # EntityTypeDefaultPermission rows cascade-delete with their PermissionCatalogEntry.


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0005_entity_type_default_permission"),
    ]

    operations = [
        migrations.RunPython(
            seed_entity_type_permissions, unseed_entity_type_permissions
        ),
    ]
