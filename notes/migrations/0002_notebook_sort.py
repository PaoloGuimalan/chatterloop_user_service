"""Notebook ordering. Prepared as source only; requires a separate schema rollout."""
from django.db import migrations, models


def backfill_sort(apps, schema_editor):
    Entry = apps.get_model("notes", "Entry")
    entries = Entry.objects.using(schema_editor.connection.alias)
    previous_entity = None
    position = 0
    pending = []
    # Increasing ranks preserve the previous descending updated_at/id display
    # order while providing deterministic ranks for equal timestamps.
    for note in entries.order_by("entity_id", "updated_at", "id").only("id", "entity_id").iterator(chunk_size=1000):
        if note.entity_id != previous_entity:
            previous_entity = note.entity_id
            position = 0
        position += 1
        note.sort = position
        pending.append(note)
        if len(pending) == 1000:
            entries.bulk_update(pending, ["sort"], batch_size=1000)
            pending = []
    if pending:
        entries.bulk_update(pending, ["sort"], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [("notes", "0001_initial")]

    operations = [
        migrations.AddField(model_name="entry", name="sort", field=models.PositiveBigIntegerField(default=0)),
        migrations.AlterField(model_name="entry", name="title", field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AlterField(model_name="entry", name="content", field=models.TextField(blank=True, default="")),
        migrations.RunPython(backfill_sort, migrations.RunPython.noop),
        migrations.AlterModelOptions(name="entry", options={"ordering": ["-sort", "-id"]}),
        migrations.RemoveIndex(model_name="entry", name="notes_entity_updated_idx"),
        migrations.AddIndex(model_name="entry", index=models.Index(fields=["entity", "-sort", "-id"], name="notes_entity_sort_idx")),
    ]
