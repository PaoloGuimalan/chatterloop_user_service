"""Link legacy override codenames to catalog IDs without discarding overrides."""

import django.db.models.deletion
from django.db import migrations, models


def recover_catalog(apps, schema_editor):
    alias = schema_editor.connection.alias
    overrides = apps.get_model("entity", "EntityPermission").objects.using(alias)
    catalog = apps.get_model("entity", "PermissionCatalogEntry").objects.using(alias)
    global_codes = set(overrides.filter(realm__isnull=True).values_list("permission", flat=True).distinct())
    realm_codes = set(overrides.filter(realm__isnull=False).values_list("permission", flat=True).distinct())
    missing = (global_codes | realm_codes) - set(catalog.values_list("codename", flat=True))
    ambiguous = missing & global_codes & realm_codes
    if ambiguous:
        raise RuntimeError(
            "Cannot infer catalog scope for legacy permissions used both globally "
            "and within realms: " + ", ".join(sorted(ambiguous)) + ". "
            "Define their intended catalog scope and reconcile conflicting overrides "
            "before retrying. No overrides have been deleted."
        )
    for codename in sorted(missing):
        catalog.create(
            codename=codename,
            scope="realm" if codename in realm_codes else "global",
            is_active=True,
            description="Recovered from existing entity permission overrides.",
        )


def link_overrides(apps, schema_editor):
    alias = schema_editor.connection.alias
    catalog = apps.get_model("entity", "PermissionCatalogEntry").objects.using(alias)
    apps.get_model("entity", "EntityPermission").objects.using(alias).update(
        permission_id=models.Subquery(
            catalog.filter(codename=models.OuterRef("legacy_permission")).values("pk")[:1]
        )
    )


def restore_codenames(apps, schema_editor):
    alias = schema_editor.connection.alias
    catalog = apps.get_model("entity", "PermissionCatalogEntry").objects.using(alias)
    apps.get_model("entity", "EntityPermission").objects.using(alias).update(
        legacy_permission=models.Subquery(
            catalog.filter(pk=models.OuterRef("permission_id")).values("codename")[:1]
        )
    )


class Migration(migrations.Migration):
    dependencies = [("entity", "0021_messages_encryption_purpose")]

    operations = [
        # Validate/recover before touching the legacy column. Recovered catalog
        # rows intentionally survive rollback; they may have gained other users.
        migrations.RunPython(recover_catalog, migrations.RunPython.noop),
        migrations.RemoveConstraint("entitypermission", "unique_entity_permission_scope"),
        migrations.RemoveIndex("entitypermission", "entity_enti_entity__2248f6_idx"),
        migrations.RemoveIndex("entitypermission", "entity_enti_entity__39ca75_idx"),
        migrations.RenameField("entitypermission", "permission", "legacy_permission"),
        # Nullable so reversing RemoveField can recreate and backfill it safely.
        migrations.AlterField("entitypermission", "legacy_permission", models.CharField(max_length=150, null=True)),
        migrations.AddField(
            "entitypermission", "permission",
            models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="entity_overrides", to="entity.permissioncatalogentry",
            ),
        ),
        migrations.RunPython(link_overrides, restore_codenames),
        migrations.AlterField(
            "entitypermission", "permission",
            models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="entity_overrides", to="entity.permissioncatalogentry",
            ),
        ),
        migrations.RemoveField("entitypermission", "legacy_permission"),
        migrations.AddIndex("entitypermission", models.Index(
            fields=["entity", "permission", "realm"], name="entity_enti_entity__2248f6_idx",
        )),
        migrations.AddIndex("entitypermission", models.Index(
            fields=["entity", "permission"], name="entity_enti_entity__39ca75_idx",
        )),
        migrations.AddConstraint("entitypermission", models.UniqueConstraint(
            fields=["entity", "permission", "realm"], name="unique_entity_permission_scope",
        )),
        migrations.AlterModelTable("permissioncatalogentry", "entity_permission_catalog"),
        migrations.AlterModelTable("entitypermission", "entity_permission"),
    ]
