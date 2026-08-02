from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0010_backfill_follows_from_connections"),
    ]

    operations = [
        # default=True is what backfills every existing row as accepted:
        # follows created before private profiles existed were never
        # gated, so they must stay usable.
        migrations.AddField(
            model_name="follow",
            name="status",
            field=models.BooleanField(default=True),
        ),
    ]
