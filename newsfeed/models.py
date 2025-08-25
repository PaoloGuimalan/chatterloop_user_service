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
    privacy_status = models.CharField(max_length=50)
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
