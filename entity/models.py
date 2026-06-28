from django.db import models
from django.db.models import Q
from django.utils.timezone import now


def build_entity_id(entity_type: str, source_id: str) -> str:
    """Canonical, deterministic entity identifier.

    This is the single Python source of truth for the entity id format. It is
    mirrored verbatim in JS (server/reusables/hooks/entity.js) and TS
    (webapp/src/reusables/hooks/entity.ts) so that any service can build an
    entity id from a known source id with no database round-trip.

    Format: ``entity:<entity_type>:<source_id>``
    Examples:
      - entity:user:<account.id>      (source_id == Account.id, a uuid)
      - entity:realm:<realm.realm_id> (source_id == Realm.realm_id, 15-digit)
    """
    return f"entity:{str(entity_type).strip().lower()}:{source_id}"


class Entity(models.Model):
    """Unified actor pointer for any account or realm that can interact.

    The deterministic ``entity_id`` string IS the primary key. It is embeddable
    verbatim into MongoDB (message sender/participants) and Cassandra
    (engagement logs) without a join, and reproducible across runtimes, so it
    needs no second surrogate identifier.
    """

    ENTITY_TYPE_USER = "user"
    ENTITY_TYPE_REALM = "realm"
    ENTITY_TYPE_CHOICES = [
        (ENTITY_TYPE_USER, "User"),
        (ENTITY_TYPE_REALM, "Realm"),
    ]

    SOURCE_TYPE_ACCOUNT = "user.account"
    SOURCE_TYPE_REALM = "community.realm"

    entity_id = models.CharField(max_length=200, primary_key=True)
    entity_type = models.CharField(
        max_length=50, db_index=True, choices=ENTITY_TYPE_CHOICES
    )

    # Polymorphic source pointer back to the owning record.
    source_type = models.CharField(max_length=100, db_index=True)
    source_id = models.CharField(max_length=150, db_index=True)

    # Concrete links so the entity row literally holds the connection to its
    # backing record (exactly one is set, matching entity_type). String refs
    # avoid an import cycle with the user/community apps.
    account = models.ForeignKey(
        "user.Account",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="entity",
    )
    realm = models.ForeignKey(
        "community.Realm",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="entity",
    )

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
                check=~Q(entity_type=""), name="entity_type_not_empty"
            ),
            models.CheckConstraint(
                check=~Q(source_type=""), name="entity_source_type_not_empty"
            ),
            models.CheckConstraint(
                check=~Q(source_id=""), name="entity_source_id_not_empty"
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.entity_id and self.entity_type and self.source_id:
            self.entity_id = build_entity_id(self.entity_type, self.source_id)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.entity_id
