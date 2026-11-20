import uuid
import random
import secrets
from django.db import models, transaction
from django.core.exceptions import ValidationError
from user.models import Account

# Follow (formerly RealmFollow) now lives in the entity app - the follower is
# an Entity, so a page can follow a page. Re-exported so existing
# `from community.models import Follow` imports keep resolving.
from entity.models import Entity, Follow  # noqa: F401
from django.utils.timezone import now
from core.profile_paths import clean_model_profile_path, validate_model_profile_path
from community.realm_types import REALM_TYPE_CHOICES


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


class Type(models.Model):
    """Fixed realm-kind references used by permission applicability."""
    code = models.CharField(max_length=150, unique=True, choices=REALM_TYPE_CHOICES)

    class Meta:
        db_table = 'community_type'
        verbose_name = 'realm type'
        verbose_name_plural = 'realm types'
        ordering = ['code']
        constraints = [models.CheckConstraint(condition=models.Q(code__in=[
            code for code, _ in REALM_TYPE_CHOICES]), name='community_type_known_code')]

    def __str__(self):
        return self.get_code_display()


class Realm(models.Model):
    REALM_TYPE_CHOICES = REALM_TYPE_CHOICES

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
        created = self._state.adding
        validate_model_profile_path(self, "slug", kwargs.get("update_fields"))
        # Enforced unconditionally, not just "if blank" - id and realm_id
        # must always be the same value, full stop.
        self.realm_id = self.id
        with transaction.atomic(using=kwargs.get("using") or self._state.db or "default"):
            super().save(*args, **kwargs)
            if created:
                from entity.roles import ensure_default_roles
                ensure_default_roles(self)

    def clean(self):
        super().clean()
        clean_model_profile_path(self, "slug")


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
    role = models.ForeignKey(
        "entity.Role", on_delete=models.PROTECT, related_name="memberships",
    )
    date_joined = models.DateTimeField(null=True)

    class Meta:
        unique_together = ("entity", "realm")

    def clean(self):
        super().clean()
        if (
            self.entity_id
            and Realm.objects.filter(
                entity_id=self.entity_id, type="community"
            ).exists()
        ):
            raise ValidationError(
                {"entity": "Communities cannot be members of any realm"}
            )

        if self.role_id and self.realm_id and self.role.realm_id != self.realm_id:
            raise ValidationError({"role": "The selected role belongs to another realm."})

    def save(self, *args, **kwargs):
        if not self.role_id and self.realm_id:
            from entity.roles import get_default_role
            self.role = get_default_role("member", realm=self.realm_id)
        self.clean()
        return super().save(*args, **kwargs)




class Invite(models.Model):
    INVITE_KIND_CHOICES = [
        ("invite", "Invite"),
        ("request", "Request"),
    ]

    INVITE_STATUS_CHOICES = [
        ("for_approval", "Awaiting approval"),
        ("rejected", "Rejected"),
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
        ("revoked", "Revoked"),
    ]

    # What accepting does - see community/invite_rules.py. "join" for a group,
    # a server or a conference; a page invites to "manage" (its team, with
    # `role`) or to "follow".
    INVITE_PURPOSE_CHOICES = [
        ("join", "Join"),
        ("manage", "Manage"),
        ("follow", "Follow"),
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
    purpose = models.CharField(
        max_length=20, choices=INVITE_PURPOSE_CHOICES, default="join"
    )
    # A "manage" invite's role: admin or moderator. Null otherwise.
    role = models.ForeignKey(
        "entity.Role", on_delete=models.PROTECT, related_name="invites",
        null=True, blank=True, default=None,
    )
    # Only what the inviter TYPED. A username invite has none - storing the
    # account's address would hand it to the inviter in every reply.
    target_email = models.EmailField(db_index=True, null=True, blank=True)
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
            models.Index(fields=["realm", "kind", "status", "-created_at"], name="realm_invite_review_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.target_email:
            self.target_email = self.target_email.strip().lower()
        super().save(*args, **kwargs)


class Approvals(models.Model):
    """Immutable human audit, with delivery bookkeeping on the same SQL row."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    realm = models.ForeignKey(Realm, on_delete=models.PROTECT, related_name="review_decisions")
    actor = models.ForeignKey(Entity, on_delete=models.PROTECT, related_name="realm_review_decisions")
    target_kind = models.CharField(max_length=10, choices=[("post", "Post"), ("invite", "Invite"), ("join", "Join")])
    target_id = models.CharField(max_length=150)
    action = models.CharField(max_length=16, choices=[(value, value.title()) for value in (
        "submit", "edit", "approve", "reject", "takedown", "revoke", "accept", "decline")])
    previous_state = models.JSONField(default=dict)
    new_state = models.JSONField(default=dict)
    reason = models.TextField(blank=True, default="")
    content_version = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=now, editable=False)
    delivery_state = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "community_approvals"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["realm", "target_kind", "target_id", "-created_at"], name="realm_review_target_idx"),
            models.Index(fields=["target_kind", "target_id", "-created_at"], name="approval_subject_idx"),
            models.Index(fields=["created_at", "id"], condition=models.Q(delivery_state__status="pending"), name="realm_review_delivery_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = type(self).objects.using(kwargs.get("using") or self._state.db).get(pk=self.pk)
            immutable = (field for field in self._meta.concrete_fields if field.name != "delivery_state")
            if any(field.get_prep_value(getattr(original, field.attname)) !=
                   field.get_prep_value(getattr(self, field.attname)) for field in immutable):
                raise ValidationError("Review decisions cannot be changed.")
        return super().save(*args, **kwargs)
