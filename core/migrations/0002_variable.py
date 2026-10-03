from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Variable",
            fields=[
                ("key", models.CharField(max_length=100, primary_key=True, serialize=False)),
                ("value", models.JSONField()),
                ("description", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
