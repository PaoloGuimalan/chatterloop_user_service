from django.contrib import admin
from .models import (
    Entity,
    PermissionCatalogEntry,
    Role,
    EntityPermission,
    EntityTypeDefinition,
    Token,
    EncryptionConfiguration,
    EncryptionPurpose,
)
from entity.forms import PermissionCatalogForm, RoleForm

admin.site.register(Entity)
admin.site.register(EntityPermission)
admin.site.register(EntityTypeDefinition)


@admin.register(PermissionCatalogEntry)
class PermissionCatalogAdmin(admin.ModelAdmin):
    form = PermissionCatalogForm
    list_display = ("codename", "scope", "is_active")
    list_filter = ("scope", "is_active")
    search_fields = ("codename",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    form = RoleForm
    list_display = ("name", "id", "is_default")
    list_filter = ("name", "is_default")


@admin.register(EncryptionPurpose)
class EncryptionPurposeAdmin(admin.ModelAdmin):
    list_display = ("code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code",)

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EncryptionConfiguration)
class EncryptionConfigurationAdmin(admin.ModelAdmin):
    list_display = ("name", "purpose_codes", "entity", "user", "is_active", "created_at")
    list_filter = ("purposes", "is_active")
    search_fields = ("name", "=entity__id", "=user__id")
    readonly_fields = tuple(field.name for field in EncryptionConfiguration._meta.fields) + ("purposes",)
    actions = None

    @admin.display(description="Purposes")
    def purpose_codes(self, obj):
        return ", ".join(purpose.code for purpose in obj.purposes.all())

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("purposes")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    """
    Read-mostly by design: a token cannot be created here, because creating one
    means SEEING the secret exactly once and the admin has nowhere safe to show
    it - it would land in browser history, in a screenshot, and in whatever
    proxy logged the response.

    Issuing is done by hand against the database for now (see the bot API
    README for the exact steps); the developer dashboard will own it later.

    What this view IS for is the operational half - finding a token, seeing
    what it may do and when it was last used, and revoking it.
    """

    list_display = (
        "name",
        "prefix",
        "entity",
        "rate_limit_int",
        "rate_limit_type",
        "is_active",
        "revoked_at",
        "expires_at",
        "last_used_at",
    )
    list_filter = ("is_active", "rate_limit_type")
    search_fields = ("name", "prefix", "entity__id")
    # The hash is not a secret, but showing it invites someone to think it is
    # usable. The prefix is the handle a human needs.
    readonly_fields = ("prefix", "token_hash", "created_at", "last_used_at")
    actions = ("revoke_selected",)

    def has_add_permission(self, request):
        return False

    @admin.action(description="Revoke selected tokens")
    def revoke_selected(self, request, queryset):
        """
        Revocation is a timestamp rather than a delete, so an incident keeps its
        audit trail: which token, whose, and when it was cut off.

        Written inline rather than in a service module because this is the only
        thing in this repo that acts on a token. Verification and authorization
        live in developer_service, which owns the API these credentials are
        for; Django owns the table and the ability to switch one off.
        """
        from django.utils.timezone import now

        stamp = now()
        count = queryset.filter(revoked_at__isnull=True).update(
            revoked_at=stamp, is_active=False
        )
        # Tokens already revoked keep their original timestamp; only reassert
        # is_active so a partially-reverted row cannot stay usable.
        queryset.filter(revoked_at__isnull=False).update(is_active=False)
        self.message_user(request, f"Revoked {count} token(s).")
