import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_seed_upload_variables"),
    ]

    operations = [
        migrations.CreateModel(
            name="Update",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "platform",
                    models.CharField(
                        choices=[("android", "Android"), ("ios", "iOS"), ("web", "Web")],
                        max_length=20,
                    ),
                ),
                ("version", models.CharField(max_length=50)),
                ("build", models.PositiveIntegerField()),
                (
                    "severity",
                    models.CharField(
                        choices=[("optional", "Optional"), ("required", "Required")],
                        default="optional",
                        max_length=20,
                    ),
                ),
                ("title", models.CharField(blank=True, default="", max_length=150)),
                ("details", models.TextField(blank=True, default="")),
                ("store_url", models.CharField(blank=True, default="", max_length=500)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "ordering": ["platform", "-build"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("platform", "build"), name="unique_update_build"
                    )
                ],
            },
        ),
    ]
