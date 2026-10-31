"""Expose entity-type defaults as a catalog-owned many-to-many relationship."""

import django.db.models.deletion
from django.db import migrations, models


def link_entity_types(apps, schema_editor):
    alias = schema_editor.connection.alias
    types = apps.get_model("entity", "EntityTypeDefinition").objects.using(alias)
    links = apps.get_model("entity", "PermissionEntityType").objects.using(alias)
    names = set(links.values_list("legacy_entity_type", flat=True).distinct()) | {"user", "bot", "realm"}
    for name in sorted(names):
        definition = types.create(name=name)
        links.filter(legacy_entity_type=name).update(entity_type_id=definition.pk)


def restore_type_names(apps, schema_editor):
    alias = schema_editor.connection.alias
    types = apps.get_model("entity", "EntityTypeDefinition").objects.using(alias)
    apps.get_model("entity", "PermissionEntityType").objects.using(alias).update(
        legacy_entity_type=models.Subquery(
            types.filter(pk=models.OuterRef("entity_type_id")).values("name")[:1]
        )
    )


class Migration(migrations.Migration):
    dependencies = [("entity", "0023_role_permissions")]

    operations = [
        migrations.CreateModel(
            name="EntityTypeDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=20, unique=True, choices=[
                    ("user", "user"), ("bot", "bot"), ("realm", "realm"),
                ])),
            ],
            options={"db_table": "entity_type"},
        ),
        # Rename, rather than discard/recreate, the existing assignment rows.
        migrations.AlterModelTable("entitytypedefaultpermission", "entity_permission_catalog_entity_types"),
        migrations.RenameModel("EntityTypeDefaultPermission", "PermissionEntityType"),
        migrations.RemoveConstraint("permissionentitytype", "unique_entity_type_permission"),
        migrations.RenameField("permissionentitytype", "entity_type", "legacy_entity_type"),
        migrations.AlterField("permissionentitytype", "legacy_entity_type", models.CharField(
            max_length=20, null=True, choices=[("user", "user"), ("bot", "bot"), ("realm", "realm")],
        )),
        migrations.AddField("permissionentitytype", "entity_type", models.ForeignKey(
            null=True, on_delete=django.db.models.deletion.PROTECT, to="entity.entitytypedefinition",
        )),
        migrations.RunPython(link_entity_types, restore_type_names),
        migrations.AlterField("permissionentitytype", "entity_type", models.ForeignKey(
            on_delete=django.db.models.deletion.PROTECT, to="entity.entitytypedefinition",
        )),
        migrations.RemoveField("permissionentitytype", "legacy_entity_type"),
        migrations.AddConstraint("permissionentitytype", models.UniqueConstraint(
            fields=["entity_type", "permission"], name="unique_entity_type_permission",
        )),
        migrations.AddField("permissioncatalogentry", "entity_types", models.ManyToManyField(
            blank=True, related_name="permissions", through="entity.PermissionEntityType",
            to="entity.entitytypedefinition",
        )),
    ]
