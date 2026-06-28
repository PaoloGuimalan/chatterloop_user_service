import uuid

from django.db import models
from django.db.models import Q
from django.utils.timezone import now

class Entity(models.Model):
    # Flexible core identity model for any actor/object that can interact.
    # Existing examples:
    # - entity_type=user,  source_type=user.account,      source_id=<account.id>
    # - entity_type=realm, source_type=community.realm,   source_id=<realm.realm_id>
    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, blank=True, primary_key=True
    )
    entity_id = models.CharField(max_length=255, unique=True, db_index=True)
    # Keep this open-ended so new actor types do not require schema changes.
    entity_type = models.CharField(max_length=50, db_index=True)

    # Polymorphic source pointer
    source_type = models.CharField(max_length=100, db_index=True)
    source_id = models.CharField(max_length=150, db_index=True)

    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "entity"
        unique_together = ("source_type", "source_id")
        indexes = [
            models.Index(fields=["entity_type"]),
            models.Index(fields=["source_type", "source_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=~Q(entity_type=""),
                name="entity_type_not_empty",
            ),
            models.CheckConstraint(
                check=~Q(source_type=""),
                name="entity_source_type_not_empty",
            ),
            models.CheckConstraint(
                check=~Q(source_id=""),
                name="entity_source_id_not_empty",
            ),
        ]

    @staticmethod
    def build_entity_id(entity_type: str, source_id: str):
        return f"entity:{str(entity_type).strip().lower()}:{source_id}"

    @staticmethod
    def build_user_entity_id(account_id: str):
        return Entity.build_entity_id("user", account_id)

    @staticmethod
    def build_realm_entity_id(realm_id: str):
        return Entity.build_entity_id("realm", realm_id)

    @classmethod
    def get_or_create_from_source(
        cls,
        *,
        entity_type: str,
        source_type: str,
        source_id: str,
        defaults: dict | None = None,
    ):
        resolved_defaults = defaults.copy() if defaults else {}
        resolved_defaults.setdefault("entity_type", entity_type)
        resolved_defaults.setdefault("source_type", source_type)
        resolved_defaults.setdefault("entity_id", cls.build_entity_id(entity_type, source_id))

        return cls.objects.get_or_create(
            source_type=source_type,
            source_id=source_id,
            defaults=resolved_defaults,
        )

    def save(self, *args, **kwargs):
        if not self.entity_id and self.entity_type and self.source_id:
            self.entity_id = self.build_entity_id(self.entity_type, self.source_id)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.entity_id
