import re
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


# Any run of whitespace - spaces, tabs, newlines - so a pasted tag normalises
# the same as a typed one.
WHITESPACE_RUN = re.compile(r"\s+")


def display_name(raw_name):
    """What people read: trimmed, runs collapsed, spaces KEPT."""
    return WHITESPACE_RUN.sub(" ", (raw_name or "").strip())


def normalize_key(raw_name):
    """The unique key: whitespace removed entirely, lowercased.

    So "news and culture", "News And Culture" and "newsandculture" are one
    interest rather than three - a user typing into the diary picker cannot
    create a near-duplicate of an existing interest just by spacing it
    differently.

    ONE function, used by BOTH Interest.save() and get_or_create_by_name().
    They previously computed this separately, and when the manager was updated
    without save(), save() silently overwrote the manager's key on the way to
    the database - a lookup would miss, the create would collide, and the only
    symptom was an IntegrityError naming a key nothing appeared to have
    written. Sharing the function is what makes that class of bug impossible.

    moderation_service/core/vocabulary.py::normalize mirrors this exactly. If
    the two disagree, that service stops finding existing rows and starts
    creating duplicates of them.
    """
    return WHITESPACE_RUN.sub("", display_name(raw_name)).lower()


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

        NAME AND KEY ARE NORMALISED DIFFERENTLY, ON PURPOSE
        ---------------------------------------------------
        `name` is what people read - in the admin, on a diary tag chip, in an
        automated report's description - so it keeps its spaces and only has
        runs collapsed: "News  and  Culture" becomes "News and Culture".

        `normalized_name` is the KEY, and it has spaces removed entirely:
        "newsandculture". That makes "news and culture", "News And Culture"
        and "newsandculture" one interest rather than three, which is the
        whole point - a user typing an interest into the diary picker cannot
        create a near-duplicate of an existing one just by spacing it
        differently.

        Anything matching on this key has to squash its input the same way.
        moderation_service/core/vocabulary.py::normalize mirrors it exactly;
        if the two ever disagree, that service stops finding existing rows and
        starts creating duplicates of them.
        """
        cleaned = display_name(raw_name)
        return self.get_or_create(
            normalized_name=normalize_key(cleaned), defaults={"name": cleaned}
        )


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
        # normalized_name is derived, never set by a caller - this is the one
        # place it is written. See normalize_key for why both it and the
        # manager go through the same function.
        self.name = display_name(self.name)
        self.normalized_name = normalize_key(self.name)

        # update_fields is honoured by save(), so a caller updating only
        # `name` would otherwise leave a stale key behind.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "name" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"normalized_name"}

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
    # A hashtag the author typed into the caption or a comment. Kept distinct
    # from "manual" - which means they picked it out of the interest picker -
    # because the two say different things about intent, and from
    # "content_processing", which is inference. A hashtag is neither: it is a
    # declaration, extracted by regex at creation time with no model involved.
    SOURCE_HASHTAG = "hashtag"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_CONTENT_PROCESSING, "Content Processing"),
        (SOURCE_HASHTAG, "Hashtag"),
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
