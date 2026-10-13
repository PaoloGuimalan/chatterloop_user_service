import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("entity", "0016_seed_developer_api_permissions")]
    operations = [
        migrations.CreateModel(name="Entry", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("title", models.CharField(max_length=255)),
            ("content", models.TextField()),
            ("search_text", models.TextField(default="")),
            ("is_private", models.BooleanField(default=True)),
            ("version", models.PositiveBigIntegerField(default=1)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("entity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notes", to="entity.entity")),
        ], options={"ordering": ["-updated_at", "-id"], "indexes": [models.Index(fields=["entity", "-updated_at", "-id"], name="notes_entity_updated_idx")]}),
        migrations.CreateModel(name="Attachment", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("file_id", models.CharField(max_length=150)),
            ("file_name", models.TextField()),
            ("file_type", models.CharField(max_length=150)),
            ("url", models.TextField()),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("note", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="notes.entry")),
        ], options={"ordering": ["created_at", "id"], "constraints": [models.UniqueConstraint(fields=("note", "file_id"), name="notes_unique_attachment_file")]}),
    ]
