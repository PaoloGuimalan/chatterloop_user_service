from django.db import migrations

COMMON = {"realm.post.submit", "realm.post.publish", "realm.invite.publish"}
REVIEW = {"realm.post.review", "realm.post.takedown", "realm.invite.review", "realm.notifications.view"}
IDENTITY = {"realm.identity.act"}
MANAGE = {"realm.roles.manage", "realm.permissions.manage", "realm.post.archive"}


def seed_policy(apps, schema_editor):
    alias = schema_editor.connection.alias
    catalog = apps.get_model("entity", "PermissionCatalogEntry").objects.using(alias)
    roles = apps.get_model("entity", "Role").objects.using(alias)
    grants = apps.get_model("entity", "RolePermission").objects.using(alias)
    entries = {}
    for code in sorted(COMMON | REVIEW | MANAGE | IDENTITY | {"realm.invite.create", "realm.member.add"}):
        entry, _ = catalog.get_or_create(codename=code, defaults={"scope": "realm", "is_active": True})
        if entry.scope != "realm":
            raise RuntimeError(f"{code} already has a conflicting scope; reconcile before migration.")
        entries[code] = entry.pk
    for role in roles.select_related("realm").iterator():
        tier = role.code if role.is_default else (role.name if role.name in {"admin", "moderator"} else "member")
        codes = set(COMMON)
        if tier in {"owner", "admin", "moderator"}:
            codes |= REVIEW
        if tier == "owner":
            codes |= MANAGE
        elif tier == "admin":
            codes |= {"realm.post.archive"}
        if role.realm.type != "community" and tier in {"owner", "admin"}:
            codes |= IDENTITY
        # Existing group/server writers allowed ordinary members to invite/add.
        if role.realm.type in {"group", "server"}:
            codes |= {"realm.invite.create", "realm.member.add"}
        for code in codes:
            grants.get_or_create(role_id=role.pk, permission_id=entries.get(code) or catalog.get(codename=code, scope="realm").pk)

    overrides = apps.get_model("entity", "EntityPermission").objects.using(alias)
    for existing in overrides.filter(permission__codename="realm.invite.create", effect="grant", realm__isnull=False).iterator():
        overrides.get_or_create(entity_id=existing.entity_id, realm_id=existing.realm_id,
                                permission_id=entries["realm.invite.publish"], defaults={
                                    "effect": "grant", "expires_at": existing.expires_at,
                                    "created_by_id": existing.created_by_id, "reason": existing.reason,
                                })


class Migration(migrations.Migration):
    dependencies = [("entity", "0026_role_identity_scope")]
    operations = [migrations.RunPython(seed_policy, migrations.RunPython.noop)]
