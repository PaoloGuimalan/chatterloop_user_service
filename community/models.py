import uuid
import random
import secrets
from django.db import models
from user.models import Account

# Follow (formerly RealmFollow) now lives in the entity app - the follower is
# an Entity, so a page can follow a page. Re-exported so existing
# `from community.models import Follow` imports keep resolving.
from entity.models import Entity, Follow  # noqa: F401
from entity.permissions import MemberRole
from django.utils.timezone import now


def generate_random_digit(digit):
    if digit < 1:
        raise ValueError("digit must be at least 1")
    start = 10 ** (digit - 1)
    end = 10**digit - 1
    return str(random.randint(start, end))


def generate_invite_token():
    return secrets.token_urlsafe(16)


def generate_realm_uid():
    # Matches the id format Node's server repo already generates for realms
    # (createRealmReusable / makeID(20)-style numeric strings) rather than a
    # uuid4 - realm_id used to independently generate this way; id is now
    # the source of that value and realm_id mirrors it (see save() below).
    return generate_random_digit(15)


class Realm(models.Model):

    REALM_TYPE_CHOICES = [
        ("page", "Page"),
        ("server", "Server"),
        ("group", "Group"),
        ("conference", "Conference"),
        ("channel", "Channel"),
        ("voice", "Voice"),
    ]

    id = models.CharField(
        max_length=150,
        default=generate_realm_uid,
        unique=True,
        blank=True,
        primary_key=True,
    )
    entity = models.OneToOneField(
        Entity, unique=True, on_delete=models.CASCADE, related_name="realms"
    )
    # Always kept equal to `id` (see save() below) - community_realm.realm_id
    # and .id must always be the same value. Node's createRealmReusable
    # (server repo) already enforces this by generating one id and writing
    # it to both columns; this mirrors that invariant on the Django side so
    # any Django-created Realm (e.g. via Realm.objects.create()) can't drift.
    realm_id = models.CharField(
        max_length=150,
        blank=True,
        unique=True,
    )
    name = models.CharField(max_length=150, null=False)
    profile = models.CharField(default="N/A")
    cover_photo = models.CharField(blank=True, null=True, default=None)
    description = models.TextField(blank=True, null=True, default=None)
    email = models.CharField(blank=True, null=True, default=None)
    slug = models.TextField(
        blank=True,
        null=True,
        default=None,
        unique=True,
    )
    created_by = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="realm_as_created_by",
    )
    type = models.CharField(max_length=150, null=False, choices=REALM_TYPE_CHOICES)
    parent = models.ForeignKey(
        "self",
        to_field="realm_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)
    is_private = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    is_temporary = models.BooleanField(default=False)
    starts_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=now)
    ranking_score = models.FloatField(default=0.0, db_index=True)

    def save(self, *args, **kwargs):
        # Enforced unconditionally, not just "if blank" - id and realm_id
        # must always be the same value, full stop.
        self.realm_id = self.id
        super().save(*args, **kwargs)


class Member(models.Model):
    member_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, blank=True, primary_key=True
    )
    # account = models.ForeignKey(
    #     Account,
    #     null=False,
    #     on_delete=models.DO_NOTHING,
    #     related_name="user_as_member",
    # )
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE)
    nickname = models.CharField(max_length=150, null=True, blank=True)
    realm = models.ForeignKey(
        Realm,
        null=False,
        on_delete=models.DO_NOTHING,
    )
    added_by = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="user_as_added_by",
    )
    role = models.CharField(
        max_length=150, choices=MemberRole.choices, default=MemberRole.MEMBER
    )
    date_joined = models.DateTimeField(null=True)

    class Meta:
        unique_together = ("entity", "realm")


class Invite(models.Model):

    INVITE_KIND_CHOICES = [
        ("invite", "Invite"),
        ("request", "Request"),
    ]

    INVITE_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
        ("revoked", "Revoked"),
    ]

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, blank=True, primary_key=True
    )
    realm = models.ForeignKey(
        Realm,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="realm_invites",
    )
    kind = models.CharField(
        max_length=150, choices=INVITE_KIND_CHOICES, default="invite"
    )
    status = models.CharField(
        max_length=150, choices=INVITE_STATUS_CHOICES, default="pending"
    )
    target_email = models.EmailField(db_index=True)
    target_entity = models.ForeignKey(
        Entity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="realm_invites_targeted",
    )
    accepted_by_entity = models.ForeignKey(
        Entity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="realm_invites_accepted",
    )
    invite_token = models.CharField(
        max_length=255, unique=True, default=generate_invite_token
    )
    created_by = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="realm_invites_created",
    )
    created_at = models.DateTimeField(default=now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["realm", "target_email"]),
            models.Index(fields=["realm", "invite_token"]),
        ]

    def save(self, *args, **kwargs):
        if self.target_email:
            self.target_email = self.target_email.strip().lower()
        super().save(*args, **kwargs)
