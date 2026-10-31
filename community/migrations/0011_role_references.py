"""Preserve membership and invitation roles as references to exact definitions."""

import django.db.models.deletion
import entity.roles
from django.db import migrations, models


def link_roles(apps, schema_editor):
    alias = schema_editor.connection.alias
    role = apps.get_model("entity", "Role").objects.using(alias)
    members = apps.get_model("community", "Member").objects.using(alias)
    invites = apps.get_model("community", "Invite").objects.using(alias)
    names = set(members.values_list("legacy_role", flat=True).distinct())
    names.update(invites.exclude(legacy_role__isnull=True).values_list("legacy_role", flat=True).distinct())
    for name in sorted(names):
        candidates = role.filter(name=name)
        definition = candidates.filter(is_default=True).first()
        if definition is None:
            matches = list(candidates[:2])
            if len(matches) > 1:
                raise RuntimeError(
                    f"Cannot map legacy role {name!r} to multiple role IDs. "
                    "Mark its intended default before retrying; no assignments were deleted."
                )
            definition = matches[0] if matches else role.create(name=name)
        members.filter(legacy_role=name).update(role_id=definition.pk)
        invites.filter(legacy_role=name).update(role_id=definition.pk)


def restore_names(apps, schema_editor):
    alias = schema_editor.connection.alias
    role = apps.get_model("entity", "Role").objects.using(alias)
    for model_name in ("Member", "Invite"):
        rows = apps.get_model("community", model_name).objects.using(alias)
        rows.update(legacy_role=models.Subquery(
            role.filter(pk=models.OuterRef("role_id")).values("name")[:1]
        ))


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0010_community_membership_capability"),
        ("entity", "0023_role_permissions"),
    ]

    operations = [
        migrations.RenameField("member", "role", "legacy_role"),
        migrations.AlterField("member", "legacy_role", models.CharField(
            max_length=150, null=True,
            choices=[("owner", "Owner"), ("admin", "Admin"), ("moderator", "Moderator"), ("member", "Member")],
        )),
        migrations.RenameField("invite", "role", "legacy_role"),
        migrations.AddField("member", "role", models.ForeignKey(
            null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name="memberships", to="entity.role",
        )),
        migrations.AddField("invite", "role", models.ForeignKey(
            blank=True, default=None, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name="invites", to="entity.role",
        )),
        migrations.RunPython(link_roles, restore_names),
        migrations.AlterField("member", "role", models.ForeignKey(
            default=entity.roles.get_default_member_role_id,
            on_delete=django.db.models.deletion.PROTECT,
            related_name="memberships", to="entity.role",
        )),
        migrations.RemoveField("member", "legacy_role"),
        migrations.RemoveField("invite", "legacy_role"),
    ]
