from django.db import migrations, models

# Duplicated from interests.services.affinity.INTERACTION_WEIGHTS["DIARY_TAG"]
# rather than imported - migrations must not depend on application code that
# can change shape later (same convention as every other data migration in
# this app).
DIARY_TAG_WEIGHT = 5.0


def backfill_diary_tag_affinity(apps, schema_editor):
    """
    Seeds EntityInterestAffinity/InterestTrendingScore from diary tag usage
    that already existed before bump_interest_affinity() was wired into
    diary tagging - without this, every pre-existing diary entry's tags
    would silently start at score=0, as if the entity had never engaged
    with them, even though they may have tagged dozens of past entries with
    the same interest.

    Mirrors bump_interest_affinity()'s math exactly: each historical
    (entity, interest) tag usage is worth DIARY_TAG_WEIGHT, summed per
    entity for personal affinity and summed platform-wide for the interest's
    global trending score.
    """
    EntryTagLink = apps.get_model("interests", "EntryTagLink")
    EntityInterestAffinity = apps.get_model("interests", "EntityInterestAffinity")
    InterestTrendingScore = apps.get_model("interests", "InterestTrendingScore")

    personal_counts = (
        EntryTagLink.objects.values("interest_id", "entry__account__entity_id")
        .annotate(usage_count=models.Count("id"))
    )
    for row in personal_counts:
        EntityInterestAffinity.objects.update_or_create(
            entity_id=row["entry__account__entity_id"],
            interest_id=row["interest_id"],
            defaults={"score": row["usage_count"] * DIARY_TAG_WEIGHT},
        )

    trending_counts = EntryTagLink.objects.values("interest_id").annotate(
        usage_count=models.Count("id")
    )
    for row in trending_counts:
        InterestTrendingScore.objects.update_or_create(
            interest_id=row["interest_id"],
            defaults={"score": row["usage_count"] * DIARY_TAG_WEIGHT},
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("interests", "0003_postinterestlink"),
        ("diary", "0002_move_tags_to_interests"),
        ("user", "0003_alter_connection_connection_id_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_diary_tag_affinity, noop_reverse),
    ]
