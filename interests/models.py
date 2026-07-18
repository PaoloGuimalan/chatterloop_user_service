import uuid

from django.db import models
from django.utils.timezone import now

from entity.models import Entity
from entity.permissions import PermissionEffect

# Ancestor walks in interest_resolver.py are bounded by this - the tree is
# meant to stay shallow (e.g. "hiking" -> "Outdoors" -> "Sports"), so this
# both keeps resolution cheap (a fixed-size select_related chain, not an
# open-ended recursive query) and guards against a data-entry mistake
# creating an accidental deep or cyclic chain.
MAX_INTEREST_DEPTH = 5


class InterestManager(models.Manager):
    def get_or_create_by_name(self, raw_name):
        """
        The single canonical way to resolve a user-supplied interest name to
        an Interest row - case/whitespace-insensitive (keyed on
        normalized_name) and race-safe (get_or_create already wraps create()
        in a transaction and retries on IntegrityError). Both of diary's
        pre-existing tag-creation code paths (EntrySerializer._handle_tags
        and DiaryCRUDView.post) used to each do this differently, with
        divergent dedup semantics - this replaces both.
        """
        cleaned = raw_name.strip()
        normalized = cleaned.lower()
        return self.get_or_create(normalized_name=normalized, defaults={"name": cleaned})


class Interest(models.Model):
    """
    The system-wide interest/topic vocabulary - formerly diary.Tag, promoted
    to a shared concept used for diary entries, posts, and per-entity
    personalization. See interests/services/interest_resolver.py for how
    entities' grants/denies resolve against this tree, and
    interest_affinity.py for the implicit engagement-driven signal.
    """

    name = models.CharField(max_length=50, unique=True)
    normalized_name = models.CharField(max_length=50, editable=False)
    # SET_NULL, not CASCADE: deleting a category should promote its children
    # to root, not destroy them - cascading would be a surprising and
    # destructive default for a taxonomy edit.
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = InterestManager()

    class Meta:
        # Physically renamed off of diary_tag (the table this model was
        # originally moved from via a state-only migration - see
        # interests/migrations/0001_initial.py) so the interests app fully
        # owns its own table, not just its ORM model - see
        # interests/migrations/0006_interests_own_physical_tables.py.
        db_table = "interests_interest"
        indexes = [
            models.Index(fields=["parent"]),
            models.Index(fields=["normalized_name"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["normalized_name"], name="unique_interest_normalized_name"),
        ]

    def save(self, *args, **kwargs):
        self.normalized_name = self.name.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class EntityInterest(models.Model):
    """
    Explicit grant/deny override for a single (entity, interest) pair.
    Modeled directly on entity.models.EntityPermission, deliberately WITHOUT
    a realm axis - interests are never realm-scoped, unlike permissions.
    Deliberately sparse: most entities have zero rows here and resolve via
    the ancestor-walk + implicit affinity fallback (see interest_resolver.py).
    """

    id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="interest_overrides")
    interest = models.ForeignKey(Interest, on_delete=models.CASCADE, related_name="entity_overrides")
    effect = models.CharField(max_length=10, choices=PermissionEffect.choices)
    reason = models.TextField(blank=True, null=True, default=None)
    created_by = models.ForeignKey(
        Entity, null=True, on_delete=models.SET_NULL, related_name="interest_overrides_created"
    )
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["entity", "interest"])]
        constraints = [
            models.UniqueConstraint(fields=["entity", "interest"], name="unique_entity_interest_scope"),
        ]


class EntityInterestAffinity(models.Model):
    """
    Implicit, engagement-derived signal for entities who've never explicitly
    granted/denied an interest - mirrors newsfeed's interaction_score_bump
    weighted-action pattern, but keyed by (entity, interest) instead of by
    connection. This is also the per-entity interest RANKING: "which
    interests has this entity mostly interacted with" is just
    EntityInterestAffinity.objects.filter(entity=X).order_by("-score").
    """

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="interest_affinities")
    interest = models.ForeignKey(Interest, on_delete=models.CASCADE, related_name="entity_affinities")
    score = models.FloatField(default=0.0, db_index=True)
    last_bumped_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entity", "interest"], name="unique_entity_interest_affinity"),
        ]


class EntryTagLink(models.Model):
    """
    Explicit through-model for diary.Entry <-> Interest, replacing the
    original implicit M2M that pointed at diary.Tag. Originally needed
    db_column pins onto diary_entry_tags's pre-existing physical columns
    (entry_id/tag_id) so the initial move required no data migration - see
    interests/migrations/0001_initial.py. Physically renamed to
    interests_entrytaglink (with tag_id -> interest_id) in
    interests/migrations/0006_interests_own_physical_tables.py so the
    interests app fully owns its own table, not just its ORM model.
    """

    entry = models.ForeignKey("diary.Entry", on_delete=models.CASCADE, db_column="entry_id")
    interest = models.ForeignKey(Interest, on_delete=models.CASCADE)

    class Meta:
        db_table = "interests_entrytaglink"
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "interest"], name="diary_entry_tags_entry_id_tag_id_b19ded5a_uniq"
            ),
        ]


class PostInterestLink(models.Model):
    """
    Explicit through-model for newsfeed.Post <-> Interest, built ready for a
    future (not-yet-built) content-processing pipeline: source distinguishes
    a user-picked tag from an automated one, and confidence is populated
    only for the latter. Nothing writes source="content_processing" today -
    this only exists so that pipeline can land later with zero schema
    change, just a different row shape.
    """

    SOURCE_MANUAL = "manual"
    SOURCE_CONTENT_PROCESSING = "content_processing"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_CONTENT_PROCESSING, "Content Processing"),
    ]

    id = models.BigAutoField(primary_key=True)
    post = models.ForeignKey("newsfeed.Post", on_delete=models.CASCADE)
    interest = models.ForeignKey(Interest, on_delete=models.CASCADE)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    # Null for manual tags (a human pick is binary, not confidence-scored) -
    # populated 0.0-1.0 once a content-processing pipeline writes rows here.
    confidence = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "interest"], name="unique_post_interest"),
        ]


class InterestTrendingScore(models.Model):
    """
    Global ranking of interests against each other ("what's trending
    platform-wide right now") - a distinct axis from EntityInterestAffinity
    above (personalization). Split into its own table rather than a field on
    Interest for the same reason newsfeed.PostScore is split from
    newsfeed.Post: the taxonomy itself (name, parent) is written rarely,
    while this score is written on every engagement event platform-wide -
    isolating that hot-write field keeps Interest reads (autocomplete,
    hierarchy resolution) unaffected by the write volume.
    """

    interest = models.OneToOneField(Interest, on_delete=models.CASCADE, related_name="trending_score")
    score = models.FloatField(default=0.0, db_index=True)
    recent_activity_boost = models.FloatField(default=1.0)
    updated_at = models.DateTimeField(auto_now=True)
