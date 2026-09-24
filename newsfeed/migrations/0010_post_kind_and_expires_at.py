"""
Post kinds (feed | moment | thought) and expiry - Moments & Thoughts, phase 1.

on_feed was a free CharField the client filled in, and the values in it are not
uniform: webapp sends "feed", the mobile profile/cover upload sends a boolean
that lands as "true", and the old migration_service import wrote whatever the
legacy data held. None of it was ever read. Every one of those rows is an
ordinary feed post, so anything that is not already a valid kind becomes
"feed" - the only value that existed in practice before moments and thoughts.

The partial index lives in 0011, built CONCURRENTLY outside a transaction, so
this migration never holds a long lock on newsfeed_post.
"""

from django.db import migrations, models


NORMALIZE_ON_FEED = """
    UPDATE newsfeed_post
    SET on_feed = 'feed'
    WHERE on_feed IS NULL
       OR on_feed NOT IN ('feed', 'moment', 'thought');
"""


class Migration(migrations.Migration):

    dependencies = [
        ("newsfeed", "0009_alter_post_deleted_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="expires_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AlterField(
            model_name="post",
            name="on_feed",
            field=models.CharField(
                choices=[
                    ("feed", "Feed"),
                    ("moment", "Moment"),
                    ("thought", "Thought"),
                ],
                default="feed",
                max_length=50,
            ),
        ),
        # Irreversible in substance (the original junk values are not kept),
        # but reversing is a no-op rather than an error so the schema half of
        # this migration can still be rolled back.
        migrations.RunSQL(NORMALIZE_ON_FEED, reverse_sql=migrations.RunSQL.noop),
    ]
