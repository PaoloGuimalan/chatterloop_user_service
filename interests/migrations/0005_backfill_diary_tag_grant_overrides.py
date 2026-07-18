from django.db import migrations

# Inline literal, not imported from entity/permissions.py::PermissionEffect -
# migrations must not depend on application code that can change shape
# later (same convention as entity/migrations/0004_seed_permission_catalog.py).
GRANT_EFFECT = "grant"


def backfill_diary_tag_grant_overrides(apps, schema_editor):
    """
    Explicitly tagging your own diary entry with an interest is a stronger,
    deliberate declaration than implicit affinity (see ensure_grant_override
    in interests/services/interest_resolver.py, now wired into diary
    tagging going forward) - this backfills the same explicit
    EntityInterest(grant) row for every (entity, interest) pair already
    established by a pre-existing diary tag, so historical diary tags count
    as real declared interests, not just an implicit affinity score.

    get_or_create, not update_or_create: never overwrites an existing
    override - an entity that already explicitly denied an interest via the
    grant/deny API keeps that deny.
    """
    EntryTagLink = apps.get_model("interests", "EntryTagLink")
    EntityInterest = apps.get_model("interests", "EntityInterest")

    pairs = EntryTagLink.objects.values("interest_id", "entry__account__entity_id").distinct()

    for row in pairs:
        entity_id = row["entry__account__entity_id"]
        EntityInterest.objects.get_or_create(
            entity_id=entity_id,
            interest_id=row["interest_id"],
            defaults={"effect": GRANT_EFFECT, "created_by_id": entity_id},
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("interests", "0004_backfill_diary_tag_affinity"),
    ]

    operations = [
        migrations.RunPython(backfill_diary_tag_grant_overrides, noop_reverse),
    ]
