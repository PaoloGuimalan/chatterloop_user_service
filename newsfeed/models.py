import uuid
import random
from django.db import models
from django.db.models import Q
from django.utils.timezone import now
from user.models import Account
from community.models import Realm
from entity.models import Entity
from interests.models import Interest

from cassandra.cqlengine import columns
from django_cassandra_engine.models import DjangoCassandraModel


def generate_random_digit(digit):
    if digit < 1:
        raise ValueError("digit must be at least 1")
    start = 10 ** (digit - 1)
    end = 10**digit - 1
    return str(random.randint(start, end))


def generate_post_id():
    return generate_random_digit(25)


class Post(models.Model):
    # "connections" is the audience a private profile writes with: visible
    # to the author's accepted contacts, nobody else. It is a distinct
    # level from "private" (author only) and from "custom" (an explicit
    # allow-list in PostPrivacy) - see newsfeed/services/post_visibility.py,
    # which is the single place these are turned into a queryset filter.
    PRIVACY_STATUS_CHOICES = [
        ("public", "Public"),
        ("connections", "Connections"),
        ("private", "Private"),
        ("custom", "Custom"),
    ]

    post_id = models.CharField(
        max_length=150,
        default=generate_post_id,
        unique=True,
        blank=True,
        primary_key=True,
    )

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="posts")
    # author_realm = models.ForeignKey(
    #     Realm,
    #     null=True,
    #     blank=True,
    #     on_delete=models.DO_NOTHING,
    #     related_name="posts",
    # )
    is_shared = models.BooleanField(default=False)
    file_type = models.CharField(max_length=50)
    caption = models.TextField(blank=True, null=True)
    content_type = models.CharField(max_length=50)
    is_tagged = models.BooleanField(default=False)
    privacy_status = models.CharField(
        max_length=50, choices=PRIVACY_STATUS_CHOICES, default="public"
    )
    is_sponsored = models.BooleanField(default=False)
    is_live = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    on_feed = models.CharField(max_length=50)
    date_posted = models.DateTimeField(default=now)
    from_system = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True, default=None)
    deleted_by = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        default=None,
        on_delete=models.DO_NOTHING,
        related_name="post_deleted_by",
    )
    interests = models.ManyToManyField(
        Interest, through="interests.PostInterestLink", blank=True, related_name="posts"
    )


class PostTag(models.Model):
    post_tag_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="tagging")
    entity = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
    )


class PostPrivacy(models.Model):
    privacy_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="privacy_users"
    )
    allowed_entity = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
    )


class PostReference(models.Model):
    reference_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="references")
    reference = models.TextField()
    caption = models.TextField(blank=True, null=True)
    reference_media_type = models.CharField(max_length=50)
    reference_name = models.TextField(blank=True, null=True)


class MapView(models.Model):
    map_view_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    post = models.OneToOneField(
        Post,
        on_delete=models.CASCADE,
        related_name="map_info",
    )
    status = models.BooleanField(default=False)
    is_stationary = models.BooleanField(default=True)
    latitude = models.FloatField(null=True, blank=True, default=None)
    longitude = models.FloatField(null=True, blank=True, default=None)


class Emoji(models.Model):
    emoji_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    emoji_title = models.CharField(max_length=20, null=False, default="none")
    emoji_content = models.CharField(max_length=20, null=False)
    emoji_tags = models.CharField(max_length=1000, null=False)
    emoji_theme = models.CharField(null=False, default="#7d7d7d")
    priority = models.IntegerField(default=0)
    animated_preview = models.CharField(default=None, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(Account, on_delete=models.DO_NOTHING)
    deleted_at = models.DateTimeField(blank=True, null=True)


class Reaction(models.Model):
    reaction_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="reactions")
    entity = models.ForeignKey(Entity, on_delete=models.DO_NOTHING)
    emoji = models.ForeignKey(Emoji, on_delete=models.DO_NOTHING, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("post", "entity")


class Comment(models.Model):
    comment_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    # Threads are flattened to TWO levels: a top-level comment (parent_comment
    # None) and its replies. Replying to a reply re-parents to that reply's
    # top-level ancestor and mentions its author instead of nesting deeper -
    # see CommentsView.post(). So `replies` is always the full thread under a
    # top-level comment and never itself has children.
    parent_comment = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="replies",
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    text = models.TextField(blank=True, null=True)
    attachment = models.TextField(null=True, blank=True)
    entity = models.ForeignKey(Entity, on_delete=models.DO_NOTHING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    deleted_by = models.ForeignKey(
        Entity,
        on_delete=models.DO_NOTHING,
        related_name="deleted_by_account",
        blank=True,
        null=True,
    )
    deleted_at = models.DateTimeField(blank=True, null=True)


class CommentReaction(models.Model):
    """
    A reaction on a COMMENT - the Reaction model above, one level down.

    Deliberately a separate table rather than a nullable `comment` column on
    Reaction: that would make Reaction's unique_together meaningless (a row
    would need to be unique per post OR per comment, which one constraint
    cannot express) and every existing post-reaction query would have to start
    filtering `comment__isnull=True`.
    """

    reaction_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    comment = models.ForeignKey(
        Comment, on_delete=models.CASCADE, related_name="reactions"
    )
    entity = models.ForeignKey(Entity, on_delete=models.DO_NOTHING)
    emoji = models.ForeignKey(Emoji, on_delete=models.DO_NOTHING, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One reaction per entity per comment - changing your mind updates the
        # row's emoji rather than adding a second (see CommentReactionsView.put).
        unique_together = ("comment", "entity")


class CommentPreviewCount(models.Model):
    """
    Per-emoji tally for a comment, the CommentReaction counterpart of
    PreviewCount.

    Created ON DEMAND by the reaction endpoints, never pre-seeded: a missing
    row and a count=0 row mean the same thing to every reader, and seeding
    would make this table comments x emojis. See newsfeed/signals.py for the
    longer version of why the post-side seeding was removed.
    """

    preview_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    comment = models.ForeignKey(
        Comment, on_delete=models.CASCADE, related_name="preview"
    )
    emoji = models.ForeignKey(Emoji, on_delete=models.CASCADE, null=True)
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            # What lets get_or_create resolve a concurrent first-reaction race
            # instead of splitting the tally across two rows.
            models.UniqueConstraint(
                fields=["comment", "emoji"],
                name="unique_comment_preview_count_per_emoji",
            )
        ]


class PreviewCount(models.Model):
    preview_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="preview")
    emoji = models.ForeignKey(Emoji, on_delete=models.CASCADE, null=True)
    count = models.IntegerField(null=False)

    class Meta:
        constraints = [
            # Rows are created on demand by the reaction endpoints now (see
            # newsfeed/signals.py for why the pre-seeding went away), so two
            # simultaneous first-reactions with the same emoji would otherwise
            # race into two rows and split the count. This is also what lets
            # get_or_create resolve that race instead of double-counting.
            models.UniqueConstraint(
                fields=["post", "emoji"], name="unique_preview_count_per_post_emoji"
            )
        ]


class CountType(models.TextChoices):
    COMMENT_CHOICE = "comment", "Comment"
    SHARE_CHOICE = "share", "Share"


class ActivityCount(models.Model):
    count_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="activity_counts"
    )
    count_type = models.CharField(choices=CountType.choices)
    count = models.IntegerField(default=0)

    class Meta:
        unique_together = ("post", "count_type")


class PostScore(models.Model):
    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name="score")
    affinity_score = models.FloatField(default=1.0)
    content_type_weight = models.FloatField(default=1.0)
    recent_update_boost = models.FloatField(default=1.0)
    likes_count = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)
    shares_count = models.PositiveIntegerField(default=0)
    ranking_score = models.FloatField(default=0.0, db_index=True)


class PostSave(models.Model):
    id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="saved_post")
    entity = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
    )
    saved_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ("post", "entity")


class NewsfeedIndex(DjangoCassandraModel):
    # The viewer_id (the person who owns this feed)
    bucket = columns.Text(partition_key=True)
    post_id = columns.Text(primary_key=True)
    created_at = columns.DateTime(primary_key=True, clustering_order="DESC")
    author_id = columns.Text()
    type = columns.Text(required=True, default="fanout")  # fanout, suggested, sponsored

    __options__ = {
        # 14 days = 14 * 24 * 60 * 60
        "default_time_to_live": 1209600,
        # Optimization: Since you delete frequently, keep the grace period low
        "gc_grace_seconds": 86400,  # 1 day (standard for high-turnover caches)
    }

    class Meta:
        get_pk_field = "post_id"

    def __str__(self):
        return f"{self.bucket} - {self.post_id} at {self.created_at}"


class TrendingPool(DjangoCassandraModel):
    # The partition key: "global", "gaming", "fitness", etc.
    # experiment between 100 or 1000 for trending post scores
    category = columns.Text(partition_key=True)

    # Unique post identifier for Postgres hydration
    post_id = columns.Text(primary_key=True)
    created_at = columns.DateTime(primary_key=True, clustering_order="DESC")
    author_id = columns.Text()

    __options__ = {
        "default_time_to_live": 259200,  # 3 days
        "gc_grace_seconds": 86400,
    }

    class Meta:
        get_pk_field = "post_id"
