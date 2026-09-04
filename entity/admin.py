from django.contrib import admin
from .models import (
    Entity,
    PermissionCatalogEntry,
    RolePermission,
    EntityPermission,
    EntityTypeDefaultPermission,
    Token,
)

admin.site.register(Entity)
admin.site.register(PermissionCatalogEntry)
admin.site.register(RolePermission)
admin.site.register(EntityPermission)
admin.site.register(EntityTypeDefaultPermission)


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
