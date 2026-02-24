from django.db import models
from user.models import Account
import uuid


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)


class Mood(models.Model):
    name = models.CharField(max_length=50, unique=True)
    emoji = models.CharField()


class Entry(models.Model):

    id = models.UUIDField(default=uuid.uuid4, primary_key=True, unique=True)
    account = models.ForeignKey(Account, null=False, on_delete=models.DO_NOTHING)
    title = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    entry_date = models.DateField()
    mood = models.ForeignKey(Mood, null=True, blank=True, on_delete=models.DO_NOTHING)
    is_private = models.BooleanField(default=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="entries")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Attachment(models.Model):

    id = models.UUIDField(default=uuid.uuid4, primary_key=True, unique=True)
    entry = models.ForeignKey(
        Entry, on_delete=models.CASCADE, related_name="attachments"
    )
    file_id = models.TextField()
    file_type = models.CharField(max_length=150)
    url = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class MapView(models.Model):
    map_view_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    entry = models.OneToOneField(
        Entry,
        on_delete=models.CASCADE,
        related_name="entry_map_info",
    )
    status = models.BooleanField(default=False)
    is_stationary = models.BooleanField(default=True)
    latitude = models.FloatField(null=True, blank=True, default=None)
    longitude = models.FloatField(null=True, blank=True, default=None)
