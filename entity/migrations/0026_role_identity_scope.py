"""Copy legacy shared role definitions and exact assignments into each realm.

Requires a coordinated cutover: old application binaries resolve shared roles
and must not run against this schema. No runtime model helpers are imported.
"""
from collections import defaultdict
import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify

BUILTINS = ("owner", "admin", "moderator", "member")


def flush_constraints(schema_editor):
    # PostgreSQL defers FK checks until commit. Complete them before the same
    # atomic migration alters Role's new columns; keep all changes rollbackable.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def backfill_identity(apps, schema_editor):
    alias = schema_editor.connection.alias
    roles = apps.get_model("entity", "Role").objects.using(alias)
    grants = apps.get_model("entity", "RolePermission").objects.using(alias)
    realms = apps.get_model("community", "Realm").objects.using(alias)
    members = apps.get_model("community", "Member").objects.using(alias)
    invites = apps.get_model("community", "Invite").objects.using(alias)
    originals = list(roles.filter(realm__isnull=True).order_by("pk"))
    defaults = {}
    for code in BUILTINS:
        matches = [r for r in originals if r.is_default and r.name == code]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one canonical shared {code} role; reconcile before migration.")
        defaults[code] = matches[0]
    if any(r.is_default and r.name not in BUILTINS for r in originals):
        raise RuntimeError("A noncanonical default role requires reconciliation before migration.")
    permissions = defaultdict(list)
    for role_id, permission_id in grants.filter(role__realm__isnull=True).values_list("role_id", "permission_id"):
        permissions[role_id].append(permission_id)
    for realm_id in realms.order_by("pk").values_list("pk", flat=True).iterator():
        mapping, used = {}, set(BUILTINS)
        for code, original in defaults.items():
            local = roles.create(name=original.name, code=code, is_default=True, realm_id=realm_id)
            mapping[original.pk] = local.pk
        for original in originals:
            if original.pk in mapping:
                continue
            # Legacy name-based owner variants were owners too. Consolidate
            # those assignments onto the one immutable realm Owner definition.
            if original.name == "owner":
                mapping[original.pk] = mapping[defaults["owner"].pk]
                continue
            stem = (slugify(original.name) or "role")[:245]
            code, index = stem, 2
            while code in used:
                code, index = f"{stem}-{index}", index + 1
            used.add(code)
            mapping[original.pk] = roles.create(name=original.name, code=code,
                is_default=False, realm_id=realm_id).pk
        links = {(mapping[old], permission_id) for old, permission_ids in permissions.items()
                 for permission_id in permission_ids}
        grants.bulk_create([grants.model(role_id=role_id, permission_id=permission_id)
                            for role_id, permission_id in sorted(links)])
        for old, new in mapping.items():
            members.filter(realm_id=realm_id, role_id=old).update(role_id=new)
            invites.filter(realm_id=realm_id, role_id=old).update(role_id=new)
    # PROTECT references fail the entire atomic migration if an unexpected
    # consumer was missed; no partially remapped schema can be committed.
    roles.filter(pk__in=[r.pk for r in originals]).delete()
    flush_constraints(schema_editor)


def restore_shared_roles(apps, schema_editor):
    """Conservative rollback of unchanged copies; divergent defaults need review."""
    alias = schema_editor.connection.alias
    roles = apps.get_model("entity", "Role").objects.using(alias)
    grants = apps.get_model("entity", "RolePermission").objects.using(alias)
    members = apps.get_model("community", "Member").objects.using(alias)
    invites = apps.get_model("community", "Invite").objects.using(alias)
    grouped, defaults = {}, {}
    for role in roles.order_by("pk"):
        permission_ids = tuple(grants.filter(role_id=role.pk).order_by("permission_id").values_list("permission_id", flat=True))
        name = role.code if role.is_default else role.name
        key = (name, role.is_default, permission_ids)
        if role.is_default and name in defaults and defaults[name] != key:
            raise RuntimeError("Configured realm defaults differ; reconcile before reverting realm ownership.")
        if role.is_default:
            defaults[name] = key
        grouped.setdefault(key, []).append(role)
    for (name, is_default, permission_ids), copies in grouped.items():
        shared = copies[0]
        roles.filter(pk=shared.pk).update(realm_id=None, name=name)
        for copy in copies[1:]:
            members.filter(role_id=copy.pk).update(role_id=shared.pk)
            invites.filter(role_id=copy.pk).update(role_id=shared.pk)
            roles.filter(pk=copy.pk).delete()
    # An empty installation still needs the historical callable's Member row.
    for name in BUILTINS:
        roles.get_or_create(name=name, is_default=True, realm_id=None,
                            defaults={"code": name})
    flush_constraints(schema_editor)


class Migration(migrations.Migration):
    dependencies = [("entity", "0025_editable_definition_names"), ("community", "0012_role_default_identity")]
    operations = [
        migrations.RemoveConstraint("role", "unique_default_role_name"),
        migrations.AddField("role", "code", models.CharField(max_length=255, null=True, editable=False)),
        migrations.AddField("role", "realm", models.ForeignKey(to="community.realm", null=True,
            on_delete=django.db.models.deletion.CASCADE, related_name="owned_roles")),
        migrations.RunPython(backfill_identity, restore_shared_roles),
        migrations.AlterField("role", "code", models.CharField(max_length=255, editable=False)),
        migrations.AlterField("role", "realm", models.ForeignKey(to="community.realm",
            on_delete=django.db.models.deletion.CASCADE, related_name="owned_roles")),
        migrations.AddConstraint("role", models.UniqueConstraint(fields=["realm", "code"], name="unique_realm_role_code")),
    ]
