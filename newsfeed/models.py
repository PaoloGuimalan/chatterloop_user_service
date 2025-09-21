import uuid
import random
from django.db import models
from django.utils.timezone import now
from user.models import Account


def generate_random_digit(digit):
    if digit < 1:
        raise ValueError("digit must be at least 1")
    start = 10 ** (digit - 1)
    end = 10**digit - 1
    return str(random.randint(start, end))


class Post(models.Model):
    PRIVACY_STATUS_CHOICES = [
        ("public", "Public"),
        ("private", "Private"),
        ("custom", "Custom"),
    ]

    post_id = models.CharField(
        max_length=150,
        default=generate_random_digit(25),
        unique=True,
        blank=True,
        primary_key=True,
    )
    user = models.ForeignKey(
        Account,
        null=False,
        on_delete=models.DO_NOTHING,
    )
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
    on_feed = models.CharField(max_length=50)
    date_posted = models.DateTimeField(default=now)
    from_system = models.BooleanField(default=False)


class PostTag(models.Model):
    post_tag_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="tags")
    user = models.ForeignKey(
        Account,
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
    allowed_user = models.ForeignKey(
        Account,
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


class Emoji(models.Model):
    emoji_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    emoji_title = models.CharField(max_length=20, null=False, default="none")
    emoji_content = models.CharField(max_length=20, null=False)
    emoji_tags = models.CharField(max_length=1000, null=False)
    emoji_theme = models.CharField(null=False, default="#7d7d7d")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(Account, on_delete=models.DO_NOTHING)
    deleted_at = models.DateTimeField(blank=True, null=True)


class Reaction(models.Model):
    reaction_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(Account, on_delete=models.DO_NOTHING)
    emoji = models.ForeignKey(Emoji, on_delete=models.DO_NOTHING, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("post", "user")


class Comment(models.Model):
    comment_id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    parent_comment = models.ForeignKey(
        "self", on_delete=models.DO_NOTHING, null=True, blank=True
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    text = models.TextField(blank=True, null=True)
    attachment = models.TextField(null=True, blank=True)
    user = models.ForeignKey(Account, on_delete=models.DO_NOTHING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_by = models.ForeignKey(
        Account, on_delete=models.DO_NOTHING, related_name="deleted_by_account"
    )
    deleted_at = models.DateTimeField(blank=True, null=True)
