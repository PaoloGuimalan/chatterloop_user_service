from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("newsfeed", "0007_commentreaction_commentpreviewcount"),
    ]

    operations = [
        # choices-only change: Django validates these in Python, so this
        # does not touch the column and no data is rewritten.
        migrations.AlterField(
            model_name="post",
            name="privacy_status",
            field=models.CharField(
                choices=[
                    ("public", "Public"),
                    ("connections", "Connections"),
                    ("private", "Private"),
                    ("custom", "Custom"),
                ],
                default="public",
                max_length=50,
            ),
        ),
    ]
