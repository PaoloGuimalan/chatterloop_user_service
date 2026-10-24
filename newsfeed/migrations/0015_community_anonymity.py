from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("newsfeed", "0014_postreference_date_posted_postreference_updated_at")
    ]
    operations = [
        migrations.AddField(
            model_name=name,
            name="is_anonymous",
            field=models.BooleanField(default=False),
        )
        for name in ("post", "comment", "reaction", "commentreaction")
    ]
