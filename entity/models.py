from django.db import models
from django.utils.timezone import now
import uuid

from entity.permissions import PermissionEffect, MemberRole


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
    scope = models.CharField(max_length=10, choices=PermissionScope.choices)
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
