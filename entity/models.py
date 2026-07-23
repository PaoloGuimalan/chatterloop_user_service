import random
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.timezone import now

from entity.permissions import PermissionEffect, MemberRole


def generate_random_digit(digit):
    if digit < 1:
        raise ValueError("digit must be at least 1")
    start = 10 ** (digit - 1)
    end = 10**digit - 1
    return str(random.randint(start, end))


def generate_connection_id():
    return generate_random_digit(20)


class EntityType(models.TextChoices):
    USER_CHOICE = "user", "user"
    BOT_CHOICE = "bot", "bot"
    REALM_CHOICE = "realm", "realm"


class Entity(models.Model):
    id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    type = models.CharField(choices=EntityType.choices)
    created_at = models.DateTimeField(auto_now_add=True)


class EntityPermission(models.Model):
    """
    Explicit grant/deny override for a single (entity, permission, realm)
    scope. Deliberately sparse: a normal entity has zero rows here and
    resolves via role/platform defaults (see entity/services/permission_resolver.py).
    Rows only exist for exceptions to those defaults.
    """

    id = models.CharField(max_length=40, default=uuid.uuid4, primary_key=True)
    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="permission_overrides"
    )
    permission = models.CharField(max_length=150)
    effect = models.CharField(max_length=10, choices=PermissionEffect.choices)
    realm = models.ForeignKey(
        "community.Realm",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="permission_overrides",
    )
    # realm=NULL -> global-scoped override; realm=<id> -> scoped to that realm only.
    reason = models.TextField(blank=True, null=True, default=None)
    created_by = models.ForeignKey(
        Entity,
        null=True,
        on_delete=models.SET_NULL,
        related_name="permission_overrides_created",
    )
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["entity", "permission", "realm"]),
            models.Index(fields=["entity", "permission"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "permission", "realm"],
                name="unique_entity_permission_scope",
            )
        ]


class PermissionScope(models.TextChoices):
    GLOBAL = "global", "Global"
    REALM = "realm", "Realm"
    ENTITY_TYPE = "entity_type", "Entity Type"
    # Future: API = "api", "Api" - additive, no migration rewrite needed later.


class PermissionCatalogEntry(models.Model):
    """
    Database-backed source of truth for the permission catalog (what used to
    be the hardcoded Permission.ALL/GLOBAL_SCOPED/REALM_SCOPED sets in
    entity/permissions.py). Editable without a code deploy - the intended
    foundation for a future systems-admin panel and the commercial API's
    growing, dynamically-defined scopes.

    entity/permissions.py's Permission class of string constants is
    deliberately kept alongside this table (not replaced) - it exists purely
    for IDE typo-safety at call sites like RequiresPermission(Permission.X);
    this table is what has_permission() actually validates against.
    """

    codename = models.CharField(max_length=150, unique=True)  # e.g. "posts.create"
    description = models.TextField(blank=True, default="")
    scope = models.CharField(max_length=20, choices=PermissionScope.choices)
    is_active = models.BooleanField(default=True)  # soft-disable without deleting
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["scope", "is_active"])]

    def __str__(self):
        return self.codename


class RolePermission(models.Model):
    """
    Role -> default permission mapping (what used to be the hardcoded
    REALM_ROLE_DEFAULT_PERMISSIONS dict). MemberRole itself stays a fixed
    Python enum (see entity/permissions.py for why) - only which
    permissions each role grants by default is database-editable.
    """

    role = models.CharField(max_length=20, choices=MemberRole.choices)
    permission = models.ForeignKey(
        PermissionCatalogEntry, on_delete=models.CASCADE, related_name="role_grants"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"], name="unique_role_permission"
            )
        ]

    def __str__(self):
        return f"{self.role} -> {self.permission.codename}"


class EntityTypeDefaultPermission(models.Model):
    """
    Entity.type -> default permission mapping, for permissions whose
    resolution depends on the ACTING entity's own fundamental type (e.g.
    "does this entity see the Page Dashboard module" vs "does this entity
    see the Diary module") rather than on a realm-scoped Member.role.

    Distinct axis from RolePermission: RolePermission answers "what can
    this entity do WITHIN a specific realm it is a member of" (e.g. can an
    admin of realm X update realm X). This model answers "what does this
    entity fundamentally get, everywhere, by virtue of being a user vs a
    realm" - independent of any specific realm the entity might currently
    be visiting or a member of. Scoped to PermissionCatalogEntry rows with
    scope=PermissionScope.ENTITY_TYPE, always checked with realm=None (same
    call shape as global-scoped permissions), resolved against this table
    keyed by entity.type instead of GLOBAL_PLATFORM_DEFAULT's predicates.

    Keyed on the coarse Entity.type (user/bot/realm), not the finer-grained
    Realm.type (page/server/group/...) - module gating is a UI-shell-level
    concern (personal vs. institutional), and drilling into Realm.type would
    cost an extra query hop on every permission check for no current
    benefit. If individual realm types ever need to diverge from their
    entity type's default, use EntityPermission's existing nullable `realm`
    FK for a per-realm exception instead of adding a new model.
    """

    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    permission = models.ForeignKey(
        PermissionCatalogEntry,
        on_delete=models.CASCADE,
        related_name="entity_type_grants",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entity_type", "permission"],
                name="unique_entity_type_permission",
            )
        ]

    def __str__(self):
        return f"{self.entity_type} -> {self.permission.codename}"


class Connection(models.Model):
    """
    Entity<->entity connection ("contacts"). Relocated here from the `user`
    app: both sides have been Entity FKs for a while, so this is a social
    graph edge between ANY two entities (user<->user, user<->realm/page),
    not a user-only concept.

    Table name is Django's default for this app/model, i.e. entity_connection.
    The physical rename off user_connection happens in entity/0008, separately
    from the state-only app move in entity/0007.
    """

    CONNECTION_TYPE_CHOICES = [
        ("single", "Single"),
    ]

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    connection_id = models.CharField(max_length=150, default=generate_connection_id)
    action_by = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="connections_as_action_by",
    )
    nickname = models.CharField(max_length=150, null=True, blank=True)
    status = models.BooleanField(default=True)
    involved_entity = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="connections_as_involved_entity",
    )
    action_date = models.DateTimeField(default=now)
    type = models.CharField(max_length=150, null=False, choices=CONNECTION_TYPE_CHOICES)

    interaction_score = models.FloatField(default=10.0)
    last_interaction_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()

        if self.type == "single":
            # Count records with the same connection_id and type "single"
            existing_connections = Connection.objects.filter(
                connection_id=self.connection_id, type="single"
            ).exclude(pk=self.pk)

            if existing_connections.count() == 2:
                raise ValidationError(
                    "Single connection can only involve two users total."
                )

            # Check if this user is already in another distinct connection record under this ID
            user_in_use = Connection.objects.filter(
                connection_id=self.connection_id,
                type="single",
                involved_entity=self.involved_entity,
            ).exclude(pk=self.pk)

            if user_in_use.exists():
                raise ValidationError(
                    "This involved user is already part of the single connection."
                )

            if self.action_by != self.involved_entity:
                # FIX: Exclude the current connection_id so reciprocal records don't block each other
                connection_triggered = (
                    Connection.objects.filter(
                        type="single",
                        involved_entity=self.involved_entity,
                        action_by=self.action_by,
                    )
                    .exclude(connection_id=self.connection_id)
                    .exclude(pk=self.pk)
                )

                if connection_triggered.exists():
                    raise ValidationError("Connection is already existing.")

                # FIX: Exclude the current connection_id here as well
                user_initiated = (
                    Connection.objects.filter(
                        type="single",
                        involved_entity=self.action_by,
                        action_by=self.involved_entity,
                    )
                    .exclude(connection_id=self.connection_id)
                    .exclude(pk=self.pk)
                )

                if user_initiated.exists():
                    raise ValidationError(
                        "This involved user has already initiated a single connection."
                    )

    def save(self, *args, **kwargs):
        self.full_clean()  # Calls clean() and validates
        super().save(*args, **kwargs)


class Follow(models.Model):
    """
    Follow edge, entity -> entity. Relocated here from the `community` app
    (where it was RealmFollow) and generalised: BOTH sides are Entities, so
    you can follow a person or a page, and a page can follow either too.

    `followee` replaces the old `realm` FK. That column used to store a
    Realm pk; it now stores an entity id, which is also what the Node server
    already passes when bumping follow interaction scores (it sends an
    entity id against the old realm_id column, so that path silently matched
    nothing before this change).

    A realm's followers are still reachable in one hop -
    `Count("entity__followers")` from a Realm queryset - because Realm.entity
    is a OneToOne to Entity, which is what `followers` now hangs off.

    Table name is Django's default for this app/model, i.e. entity_follow.
    """

    follow_id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    follower = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.CASCADE,
        related_name="realm_follows",
    )
    followee = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.CASCADE,
        related_name="followers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    interaction_score = models.FloatField(default=10.0)
    last_interaction_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("follower", "followee")
