"""
Post.details - kind-specific settings for moments and thoughts (a thought's
mood, a moment's "allow replies & reactions"). Nullable with no default, so
adding it rewrites no rows and takes no long lock.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("newsfeed", "0011_post_ephemeral_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="details",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
