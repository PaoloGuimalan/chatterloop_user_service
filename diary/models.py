from django.db import models
from user.models import Account
import uuid

class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

class Entry(models.Model):

    id = models.UUIDField(default=uuid.uuid4, primary_key=True, unique=True)
    account = models.ForeignKey(Account, null=False, on_delete=models.DO_NOTHING)
    title = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    entry_date = models.DateField()
    mood = models.CharField(max_length=20, blank=True)  # or choices/int
    is_private = models.BooleanField(default=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="entries")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class Attachment(models.Model):

    id = models.UUIDField(default=uuid.uuid4, primary_key=True, unique=True)
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="attachments")
    url = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
