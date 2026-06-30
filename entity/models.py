from django.db import models
import uuid


class EntityType(models.TextChoices):
    USER_CHOICE = "user", "user"
    BOT_CHOICE = "bot", "bot"
    REALM_CHOICE = "realm", "realm"


class Entity(models.Model):
    id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    type = models.CharField(choices=EntityType.choices)
    created_at = models.DateTimeField(auto_now_add=True)
