from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("diary", "0001_initial"),
        ("interests", "0001_initial"),
    ]

    operations = [
        # All state-only - diary_tag and diary_entry_tags keep existing
        # physically exactly as they are; only Django's bookkeeping of
        # "diary.Tag" -> "interests.Interest" changes. Order matters:
        # Entry.tags must stop referencing Tag before Tag is deleted from
        # state, then get re-added pointing at Interest/EntryTagLink.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(model_name="entry", name="tags"),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="Tag"),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name="entry",
                    name="tags",
                    field=models.ManyToManyField(
                        blank=True,
                        related_name="entries",
                        through="interests.EntryTagLink",
                        to="interests.interest",
                    ),
                ),
            ],
        ),
    ]
