import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Lower, Trim


def backfill_normalized_name(apps, schema_editor):
    Interest = apps.get_model("interests", "Interest")
    Interest.objects.update(normalized_name=Lower(Trim("name")))


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("interests", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="interest",
            name="normalized_name",
            field=models.CharField(editable=False, max_length=50, null=True),
        ),
        migrations.RunPython(backfill_normalized_name, noop_reverse),
        migrations.AlterField(
            model_name="interest",
            name="normalized_name",
            field=models.CharField(editable=False, max_length=50),
        ),
        migrations.AddConstraint(
            model_name="interest",
            constraint=models.UniqueConstraint(
                fields=("normalized_name",), name="unique_interest_normalized_name"
            ),
        ),
        migrations.AddField(
            model_name="interest",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="children",
                to="interests.interest",
            ),
        ),
        migrations.AddIndex(
            model_name="interest",
            index=models.Index(fields=["parent"], name="diary_tag_parent__7aae4d_idx"),
        ),
        migrations.AddIndex(
            model_name="interest",
            index=models.Index(fields=["normalized_name"], name="diary_tag_normali_1c6f04_idx"),
        ),
        migrations.AddField(
            model_name="interest",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
