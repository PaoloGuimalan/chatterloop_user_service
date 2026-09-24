"""
Partial index for moments/thoughts, built without locking newsfeed_post.

A plain AddIndex takes a SHARE lock for the whole build, blocking every post
insert and update meanwhile. CONCURRENTLY does not, but it cannot run inside a
transaction - hence its own migration with atomic = False.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("newsfeed", "0010_post_kind_and_expires_at"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="post",
            index=models.Index(
                condition=models.Q(("on_feed", "feed"), _negated=True),
                fields=["entity", "on_feed", "expires_at"],
                name="newsfeed_post_ephemeral_idx",
            ),
        ),
    ]
