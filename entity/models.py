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

    # False means the edge exists but is still awaiting the followee's
    # approval - the "requested" state a private profile creates. Public
    # targets auto-approve on create, so the overwhelming majority of rows
    # are True and the default keeps every pre-existing follow accepted.
    #
    # A pending row is deliberately a real row rather than a separate
    # request table: unfollow/cancel is then the same delete, and the
    # unique_together below still stops a double-tap from queueing two
    # requests. Everything that treats a follow as a *relationship*
    # (follower counts, feed fan-out, private-profile access) must filter
    # status=True - see get_follower_ids / accepted_follow_exists.
    status = models.BooleanField(default=True)

    interaction_score = models.FloatField(default=10.0)
    last_interaction_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("follower", "followee")


class Block(models.Model):
    """
    Block edge, entity -> entity. Relocated here from the `user` app; both
    sides were already Entity FKs, so a page can be blocked exactly like a
    person and a page you administer can block on its own behalf.

    Table is Django's default for this app/model, i.e. entity_block.
    """

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    blocker = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="blocks_made",
    )
    blocked = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="blocked_by",
    )
    created_at = models.DateTimeField(default=now)

    class Meta:
        unique_together = ("blocker", "blocked")

    def __str__(self):
        return f"{self.blocker_id} blocked {self.blocked_id}"


class Report(models.Model):
    """
    Abuse report against any entity, optionally narrowed to one piece of
    content that entity owns.

    Two axes, deliberately kept separate:

    * `reported_entity` is always the entity held responsible - the account,
      the page, the realm. This is what moderation acts on, and it is what
      makes the model generic: a post report and a profile report both land
      on the same entity.
    * `target_type` / `target_id` narrow the report to the specific artefact.
      `target_id` is NULL when the whole entity is the subject (target_type
      "user" or "realm"), and holds the post/comment/message id otherwise.

    Relocated here from the `user` app; the FKs were already entity-keyed.
    Table is Django's default for this app/model, i.e. entity_report.
    """

    TARGET_TYPE_CHOICES = [
        ("user", "User"),
        ("realm", "Realm"),
        ("post", "Post"),
        ("comment", "Comment"),
        ("message", "Message"),
    ]

    # Target types that name the entity itself rather than one of its
    # artefacts - for these, target_id carries no extra information and is
    # normalised to NULL on write (see entity.services.reporting).
    ENTITY_LEVEL_TARGET_TYPES = ("user", "realm")

    REASON_CHOICES = [
        ("spam", "Spam"),
        ("harassment", "Harassment or bullying"),
        ("hate_speech", "Hate speech"),
        ("violence", "Violence or dangerous behavior"),
        ("nudity", "Nudity or sexual content"),
        ("csae", "Child sexual abuse or exploitation"),
        ("impersonation", "Impersonation"),
        ("misinformation", "Misinformation"),
        ("other", "Other"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("reviewed", "Reviewed"),
        ("actioned", "Actioned"),
        ("dismissed", "Dismissed"),
    ]

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    reporter = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="reports_filed",
    )
    reported_entity = models.ForeignKey(
        Entity,
        null=False,
        on_delete=models.DO_NOTHING,
        related_name="reports_received",
    )
    target_type = models.CharField(max_length=20, choices=TARGET_TYPE_CHOICES)
    target_id = models.CharField(max_length=150, null=True, blank=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(default=now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        Entity,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        related_name="reports_reviewed",
    )

    class Meta:
        ordering = ["-created_at"]
        # Names are pinned rather than auto-derived so the migration that
        # creates them (entity/0014) stays byte-stable - an auto name is a
        # hash of the model/field set and would drift on any later rename.
        indexes = [
            models.Index(
                fields=["status", "created_at"], name="report_status_created_idx"
            ),
            models.Index(
                fields=["reported_entity", "status"], name="report_entity_status_idx"
            ),
        ]

    def __str__(self):
        return (
            f"{self.reporter_id} reported {self.target_type}:"
            f"{self.target_id or self.reported_entity_id}"
        )


class Token(models.Model):
    """
    A long-lived API credential belonging to an Entity - table `entity_token`.

    WHY THIS EXISTS
    ---------------
    Every inbound credential this platform understood was shaped like a person
    at a browser: `AutheticationBackend` (and its Node twin `jwtchecker`) needs
    an `origin`, a replay-checked `x-nonce`, a `device-token` matching a live
    `UserSessions` row, and an `x-access-token` whose `userID` resolves in
    `user_account`. A bot fails the last two structurally, not incidentally -
    it has no account and no device - which is what `bot/models.py` meant by
    "Authentication for user-owned bots is a separate problem and is not solved
    here". This is that problem, solved.

    Named `Token` rather than `EntityToken` on purpose: Django derives the
    table from app + model, so `entity.Token` is physically `entity_token`
    while `entity.EntityToken` would be `entity_entitytoken`. Migration 0013
    established that this app does not pin `db_table` and takes Django's
    default, so the model name is the only lever on the physical name - and
    the physical name is the part other services type by hand.

    WHY THE SECRET IS NOT HERE
    --------------------------
    `token_hash` is SHA-256 of the whole token string; the token itself is
    returned exactly once, at issue, and is unrecoverable afterwards. A plain
    hash with no salt or stretching is correct HERE and would not be for a
    password: this secret is 32 bytes from `secrets.token_urlsafe`, so there is
    no dictionary to run and no work factor that would meaningfully slow an
    attacker who already has the column.

    `prefix` exists so that verification is one indexed lookup rather than a
    scan-and-hash over every row: the token carries its own prefix in the
    clear, the prefix finds at most one row, and only then is a hash compared -
    in constant time, so a near-miss leaks nothing through timing.

    VERIFICATION LIVES ELSEWHERE
    ---------------------------
    Nothing in this repo reads this table at request time. developer_service
    (Go) verifies these tokens and enforces their scopes; Django owns the
    schema, the migrations and the ability to revoke a row from admin. The
    token FORMAT is deliberately trivial - all hex, plain SHA-256, no salt or
    framework encoding - precisely so the implementation that does the checking
    can live in another language without a subtle disagreement.

    WHAT `scopes` MEANS
    -------------------
    The permission codenames this token may exercise, from the same catalog
    (`entity_permissioncatalogentry`) that every other permission check in the
    platform reads. It is a CEILING, not a grant. A request is allowed only if
    the scope is on the token AND `has_permission(entity, codename, realm)`
    passes for the owning entity, so:

      * a leaked token can never do more than its entity could;
      * revoking a permission from the entity instantly narrows every token it
        owns, with no token edit and no revocation list to fan out;
      * a token can be deliberately narrower than its entity - a read-only
        token for a bot that is otherwise allowed to post.

    Stored as JSON rather than a join table to match `EntityPermission`, which
    also holds a bare codename string rather than a FK to the catalog.
    """

    id = models.CharField(
        max_length=40, default=uuid.uuid4, unique=True, primary_key=True
    )

    entity = models.ForeignKey(
        Entity, on_delete=models.CASCADE, related_name="tokens"
    )

    # What this credential is for, in the words of whoever issued it. Shown in
    # admin and returned by the introspection endpoint, because "which of these
    # six tokens is the one the RAG pipeline uses" is otherwise unanswerable.
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")

    # The clear-text lookup half. Unique so a collision is a database error at
    # issue time rather than an ambiguous verify later.
    prefix = models.CharField(max_length=16, unique=True, db_index=True)

    # SHA-256 hex of the full token string, so exactly 64 characters.
    token_hash = models.CharField(max_length=64)

    scopes = models.JSONField(default=list, blank=True)

    # Optional confinement to a single realm. NULL means the token is not
    # realm-confined and realm-scoped permissions resolve per request; a value
    # pins every check on this token to that realm, so a token issued for one
    # community cannot act in another even if its entity is a member of both.
    realm = models.ForeignKey(
        "community.Realm",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="entity_tokens",
    )

    created_by = models.ForeignKey(
        Entity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tokens_created",
    )

    created_at = models.DateTimeField(default=now)

    # NULL means no expiry. Allowed, and deliberately not the default the
    # issuing helper hands out - a service credential that never expires is a
    # decision someone should have to make, not one they fall into.
    expires_at = models.DateTimeField(null=True, blank=True)

    # Written on use, throttled (see entity/services/tokens.py) so a busy token
    # does not turn every authenticated read into a write. Its purpose is
    # answering "is this credential still in use" before revoking it, which
    # minute-level accuracy serves perfectly well.
    last_used_at = models.DateTimeField(null=True, blank=True)

    # Revocation is a timestamp rather than a delete so that an incident has an
    # audit trail: which token, whose, and when it was cut off.
    revoked_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["entity", "is_active"], name="entity_token_owner_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= now()

    @property
    def is_usable(self) -> bool:
        """Live, unrevoked and unexpired. Says nothing about scopes."""
        return self.is_active and self.revoked_at is None and not self.is_expired

    def has_scope(self, codename: str) -> bool:
        """Whether this token carries `codename`.

        Only half of an authorization decision - the entity must also hold the
        permission. Both halves are enforced in services/developer_service
        (internal/auth), which owns the API these credentials authenticate to;
        this repo owns the table and nothing else about them.
        """
        scopes = self.scopes or []
        return codename in scopes
