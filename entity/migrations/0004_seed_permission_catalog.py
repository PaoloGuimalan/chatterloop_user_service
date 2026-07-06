from django.db import migrations

# Inline literal seed data, not imported from entity/permissions.py -
# migrations must never depend on application code that can change shape
# later. This is a frozen, correct-at-time-of-writing snapshot of the
# catalog + role matrix that existed in entity/permissions.py as of writing
# this migration, so behavior is unchanged the moment this ships.

GLOBAL_PERMISSIONS = [
    "messages.send",
    "messages.react",
    "conversations.create",
    "posts.create",
    "comments.create",
    "contacts.request.create",
    "poke.create",
    "ai.prompt.assist",
    "webrtc.call.join",
    "realm.server.create",
]

REALM_PERMISSIONS = [
    "realm.update",
    "realm.invite.create",
    "realm.invite.revoke",
    "realm.request.approve",
    "realm.request.decline",
    "realm.member.view",
    "realm.member.add",
    "realm.member.remove",
    "realm.member.role.update",
    "realm.follower.remove",
    "realm.channel.create",
    "realm.media.update",
    "realm.delete",
    "realm.ownership.transfer",
]

ROLE_DEFAULT_PERMISSIONS = {
    "owner": [
        "realm.update",
        "realm.invite.create",
        "realm.invite.revoke",
        "realm.request.approve",
        "realm.request.decline",
        "realm.member.view",
        "realm.member.add",
        "realm.member.remove",
        "realm.member.role.update",
        "realm.follower.remove",
        "realm.channel.create",
        "realm.media.update",
        "realm.delete",
        "realm.ownership.transfer",
    ],
    "admin": [
        "realm.update",
        "realm.invite.create",
        "realm.invite.revoke",
        "realm.request.approve",
        "realm.request.decline",
        "realm.member.view",
        "realm.member.add",
        "realm.member.remove",
        "realm.member.role.update",
        "realm.follower.remove",
        "realm.channel.create",
        "realm.media.update",
    ],
    "moderator": [
        "realm.request.approve",
        "realm.request.decline",
        "realm.member.view",
    ],
    "member": [
        "realm.member.view",
    ],
}


def seed_catalog(apps, schema_editor):
    PermissionCatalogEntry = apps.get_model("entity", "PermissionCatalogEntry")
    RolePermission = apps.get_model("entity", "RolePermission")

    entries_by_codename = {}
    for codename in GLOBAL_PERMISSIONS:
        entries_by_codename[codename] = PermissionCatalogEntry.objects.create(
            codename=codename, scope="global"
        )
    for codename in REALM_PERMISSIONS:
        entries_by_codename[codename] = PermissionCatalogEntry.objects.create(
            codename=codename, scope="realm"
        )

    for role, codenames in ROLE_DEFAULT_PERMISSIONS.items():
        for codename in codenames:
            RolePermission.objects.create(
                role=role, permission=entries_by_codename[codename]
            )


def unseed_catalog(apps, schema_editor):
    PermissionCatalogEntry = apps.get_model("entity", "PermissionCatalogEntry")
    all_codenames = GLOBAL_PERMISSIONS + REALM_PERMISSIONS
    PermissionCatalogEntry.objects.filter(codename__in=all_codenames).delete()
    # RolePermission rows cascade-delete with their PermissionCatalogEntry.


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0003_permissioncatalogentry_rolepermission"),
    ]

    operations = [
        migrations.RunPython(seed_catalog, unseed_catalog),
    ]
