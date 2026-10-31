"""Group legacy role grants into role definitions with a many-to-many field."""

import django.db.models.deletion
from django.db import migrations, models


def link_roles(apps, schema_editor):
    alias = schema_editor.connection.alias
    role = apps.get_model("entity", "Role").objects.using(alias)
    links = apps.get_model("entity", "RolePermission").objects.using(alias)
    builtin_names = {"owner", "admin", "moderator", "member"}
    names = set(links.values_list("legacy_role", flat=True).distinct()) | builtin_names
    for name in sorted(names):
        definition = role.create(name=name, is_default=name in builtin_names)
        links.filter(legacy_role=name).update(role_id=definition.pk)


def restore_names(apps, schema_editor):
    alias = schema_editor.connection.alias
    role = apps.get_model("entity", "Role").objects.using(alias)
    links = apps.get_model("entity", "RolePermission").objects.using(alias)
    duplicates = list(links.values("role__name", "permission_id").annotate(
        count=models.Count("id"),
    ).filter(count__gt=1).values_list("role__name", flat=True).distinct())
    if duplicates:
        raise RuntimeError(
            "Cannot restore the legacy role mapping while same-named role definitions "
            "share permissions: " + ", ".join(sorted(duplicates)) + ". "
            "Reconcile those definitions before rolling back; no grants were deleted."
        )
    links.update(legacy_role=models.Subquery(
        role.filter(pk=models.OuterRef("role_id")).values("name")[:1]
    ))


class Migration(migrations.Migration):
    dependencies = [("entity", "0022_entitypermission_catalog")]

    operations = [
        migrations.CreateModel(
            name="Role",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150, choices=[
                    ("owner", "Owner"), ("admin", "Admin"),
                    ("moderator", "Moderator"), ("member", "Member"),
                ])),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "entity_role", "constraints": [models.UniqueConstraint(
                fields=["name"], condition=models.Q(is_default=True), name="unique_default_role_name",
            )]},
        ),
        migrations.RemoveConstraint("rolepermission", "unique_role_permission"),
        migrations.RenameField("rolepermission", "role", "legacy_role"),
        migrations.AlterField("rolepermission", "legacy_role", models.CharField(
            max_length=20, null=True, choices=[
                ("owner", "Owner"), ("admin", "Admin"),
                ("moderator", "Moderator"), ("member", "Member"),
            ],
        )),
        migrations.AddField("rolepermission", "role", models.ForeignKey(
            null=True, on_delete=django.db.models.deletion.CASCADE,
            related_name="permission_links", to="entity.role",
        )),
        migrations.RunPython(link_roles, restore_names),
        migrations.AlterField("rolepermission", "role", models.ForeignKey(
            on_delete=django.db.models.deletion.CASCADE,
            related_name="permission_links", to="entity.role",
        )),
        migrations.RemoveField("rolepermission", "legacy_role"),
        migrations.AlterModelTable("rolepermission", "entity_role_permissions"),
        migrations.AddConstraint("rolepermission", models.UniqueConstraint(
            fields=["role", "permission"], name="unique_role_permission",
        )),
        migrations.AddField("role", "permissions", models.ManyToManyField(
            blank=True, related_name="roles", through="entity.RolePermission",
            to="entity.permissioncatalogentry",
        )),
    ]
