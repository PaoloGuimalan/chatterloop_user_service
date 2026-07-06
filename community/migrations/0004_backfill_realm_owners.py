from django.db import migrations


def backfill_owners(apps, schema_editor):
    """
    Promotes each existing realm's creator from 'admin' to 'owner'.
    Realm creation historically (and still, via the Node `server` repo's
    createRealmReusable) inserted the creator as role='admin' - this repo's
    grep-based check for existing Realm/Member rows only covered Django-side
    code and missed that Node writes to these same tables directly. As of
    this migration there are 11 realms / 57 members in production, all with
    role in {'admin', 'member'}; this promotes only the creator's own row.
    """
    Realm = apps.get_model("community", "Realm")
    Member = apps.get_model("community", "Member")

    for realm in Realm.objects.all():
        Member.objects.filter(
            realm=realm, entity_id=realm.created_by_id, role="admin"
        ).update(role="owner")


def reverse_backfill_owners(apps, schema_editor):
    Member = apps.get_model("community", "Member")
    Member.objects.filter(role="owner").update(role="admin")


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0003_alter_member_role"),
    ]

    operations = [
        migrations.RunPython(backfill_owners, reverse_backfill_owners),
    ]
