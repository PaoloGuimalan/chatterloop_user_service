import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    related_name only - `comment_set` -> `replies`. No column, index or
    constraint changes; the FK itself is untouched. Needed purely so Django's
    migration state matches the model and `makemigrations --check` stays
    quiet.
    """

    dependencies = [
        ('newsfeed', '0004_post_interests'),
    ]

    operations = [
        migrations.AlterField(
            model_name='comment',
            name='parent_comment',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='replies', to='newsfeed.comment'),
        ),
    ]
