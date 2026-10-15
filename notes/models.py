import uuid
from django.db import models


class Entry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey("entity.Entity", on_delete=models.CASCADE, related_name="notes")
    title = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField(blank=True, default="")
    search_text = models.TextField(default="")
    is_private = models.BooleanField(default=True)
    version = models.PositiveBigIntegerField(default=1)
    sort = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-sort", "-id"]
        indexes = [models.Index(fields=["entity", "-sort", "-id"], name="notes_entity_sort_idx")]


class Attachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    note = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="attachments")
    file_id = models.CharField(max_length=150)
    file_name = models.TextField()
    file_type = models.CharField(max_length=150)
    # Completed upload URL, returned to clients and used by media cleanup.
    url = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["note", "file_id"], name="notes_unique_attachment_file")]
        ordering = ["created_at", "id"]
