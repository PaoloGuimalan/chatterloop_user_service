# PreviewCount rows are created on demand now instead of being pre-seeded for
# every (post, emoji) pair - see newsfeed/signals.py. That makes the pair
# genuinely unique-by-intent, and the constraint is what lets get_or_create
# resolve a concurrent first-reaction race rather than splitting the count
# across two rows.
#
# Existing duplicates have to go first or AddConstraint fails. They should be
# rare: the reaction endpoints used .get(), which raises MultipleObjectsReturned
# on a duplicate, so any post/emoji pair with two rows was already 500ing on
# every reaction. The likely source is the old per-emoji signal running twice
# for one emoji, which leaves extra count=0 strays.

from django.db import migrations, models
from django.db.models import Count


def dedupe_preview_counts(apps, schema_editor):
    PreviewCount = apps.get_model("newsfeed", "PreviewCount")

    duplicated_pairs = (
        PreviewCount.objects.values("post_id", "emoji_id")
        .annotate(row_count=Count("preview_id"))
        .filter(row_count__gt=1)
    )

    for pair in duplicated_pairs:
        group = PreviewCount.objects.filter(
            post_id=pair["post_id"], emoji_id=pair["emoji_id"]
        ).order_by("-count", "preview_id")

        # Highest count wins: a real tally beats the count=0 strays a repeated
        # seeding run would have left behind. preview_id breaks ties so the
        # choice is deterministic rather than depending on row order.
        keeper = group.first()
        if keeper:
            group.exclude(preview_id=keeper.preview_id).delete()


def noop_reverse(apps, schema_editor):
    """
    Nothing to undo: the deleted rows were duplicates of a row that is still
    there, and re-creating them would only re-break the constraint.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("newsfeed", "0005_comment_parent_related_name"),
    ]

    operations = [
        migrations.RunPython(dedupe_preview_counts, noop_reverse),
        migrations.AddConstraint(
            model_name="previewcount",
            constraint=models.UniqueConstraint(
                fields=("post", "emoji"), name="unique_preview_count_per_post_emoji"
            ),
        ),
    ]
