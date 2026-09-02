"""
Single source of truth for the entity permissions catalog, shared across
this repo (Django) and the Node `server` repo (which hand-ports the same
constants/priority logic in reusables/hooks/permissionChecker.js against
the same Postgres tables). Keep both in sync when editing this file.
"""

from django.db import models


class MemberRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MODERATOR = "moderator", "Moderator"
    MEMBER = "member", "Member"


class PermissionEffect(models.TextChoices):
    GRANT = "grant", "Grant"
    DENY = "deny", "Deny"


class Permission:
    """Dotted permission-string constants. Import Permission.X rather than
    hardcoding string literals anywhere else in the codebase."""

    # --- Global-scope capabilities (revocable per-entity regardless of realm) ---
    MESSAGES_SEND = "messages.send"
    MESSAGES_REACT = "messages.react"
    CONVERSATIONS_CREATE = "conversations.create"
    POSTS_CREATE = "posts.create"
    COMMENTS_CREATE = "comments.create"
    CONTACTS_REQUEST_CREATE = "contacts.request.create"
    POKE_CREATE = "poke.create"
    AI_PROMPT_ASSIST = "ai.prompt.assist"
    WEBRTC_CALL_JOIN = "webrtc.call.join"
    REALM_SERVER_CREATE = "realm.server.create"
    # ^ Despite the "realm." prefix (kept for naming consistency with the
    # other realm.* permissions), this is GLOBAL-scoped: creating a brand new
    # top-level server has no existing realm to check membership/role
    # against, so it resolves via GLOBAL_PLATFORM_DEFAULT like the other
    # global capabilities, not via REALM_ROLE_DEFAULT_PERMISSIONS.

    # --- Developer API capabilities ---
    # Declared here because this repo owns the permission catalog; they are
    # ENFORCED in services/developer_service, which owns the API they gate.
    # Nothing in this repo checks them.
    # Deliberately absent from GLOBAL_PLATFORM_DEFAULT below, which makes them
    # DENY-BY-DEFAULT: has_permission() falls through to `return False` when a
    # global permission has no default predicate, so these require an explicit
    # EntityPermission grant row on the entity that is to hold them.
    #
    # Every other global permission defaults to _account_in_good_standing,
    # which returns True for any entity WITHOUT an Account - realms and bots
    # both - so seeding these the same way would have handed them to every bot
    # on the platform the moment they were created. A capability that only
    # exists to be delegated to a machine should be granted on purpose.
    MESSAGES_READ = "messages.read"
    NOTIFICATIONS_READ = "notifications.read"
    EVENTS_SUBSCRIBE = "events.subscribe"

    # --- Realm-scope capabilities (resolved via Member.role unless overridden) ---
    REALM_UPDATE = "realm.update"
    REALM_INVITE_CREATE = "realm.invite.create"
    REALM_INVITE_REVOKE = "realm.invite.revoke"
    REALM_REQUEST_APPROVE = "realm.request.approve"
    REALM_REQUEST_DECLINE = "realm.request.decline"
    REALM_MEMBER_VIEW = "realm.member.view"
    REALM_MEMBER_ADD = "realm.member.add"
    # ^ Defined in the catalog but deliberately NOT wired onto the Node
    # /m/addnewmember or /s/addnewmembertoserver routes: both currently let
    # any existing member add others (not just admins), and narrowing that
    # to admin/owner-only would be a product behavior change, not a security
    # fix. Confirmed with the product owner to leave as-is - don't "fix"
    # this into consistency without re-confirming the intended behavior.
    REALM_MEMBER_REMOVE = "realm.member.remove"
    REALM_MEMBER_ROLE_UPDATE = "realm.member.role.update"
    REALM_FOLLOWER_REMOVE = "realm.follower.remove"
    REALM_CHANNEL_CREATE = "realm.channel.create"
    # ^ Scoped to the PARENT server realm - creating a channel happens inside
    # an existing server, so membership/role there is exactly what should
    # gate it (unlike REALM_SERVER_CREATE above).
    REALM_MEDIA_UPDATE = "realm.media.update"
    REALM_DELETE = "realm.delete"
    REALM_OWNERSHIP_TRANSFER = "realm.ownership.transfer"

    # --- Entity-type-scope capabilities (resolved via the acting entity's own
    # .type - user vs realm - unlike REALM_SCOPED above, which is about a
    # Member.role within some OTHER realm being visited/administered) ---
    MODULE_DIARY_ACCESS = "module.diary.access"
    MODULE_POKE_ACCESS = "module.poke.access"
    MODULE_CONTACTS_ACCESS = "module.contacts.access"
    MODULE_PAGE_DASHBOARD_ACCESS = "module.page_dashboard.access"
    MODULE_PAGE_MEMBERS_ACCESS = "module.page_members.access"
    MODULE_PAGE_INVITES_ACCESS = "module.page_invites.access"

    GLOBAL_SCOPED = {
        MESSAGES_SEND,
        MESSAGES_REACT,
        CONVERSATIONS_CREATE,
        POSTS_CREATE,
        COMMENTS_CREATE,
        CONTACTS_REQUEST_CREATE,
        POKE_CREATE,
        AI_PROMPT_ASSIST,
        WEBRTC_CALL_JOIN,
        REALM_SERVER_CREATE,
        MESSAGES_READ,
        NOTIFICATIONS_READ,
        EVENTS_SUBSCRIBE,
    }

    REALM_SCOPED = {
        REALM_UPDATE,
        REALM_INVITE_CREATE,
        REALM_INVITE_REVOKE,
        REALM_REQUEST_APPROVE,
        REALM_REQUEST_DECLINE,
        REALM_MEMBER_VIEW,
        REALM_MEMBER_ADD,
        REALM_MEMBER_REMOVE,
        REALM_MEMBER_ROLE_UPDATE,
        REALM_FOLLOWER_REMOVE,
        REALM_CHANNEL_CREATE,
        REALM_MEDIA_UPDATE,
        REALM_DELETE,
        REALM_OWNERSHIP_TRANSFER,
    }

    ENTITY_TYPE_SCOPED = {
        MODULE_DIARY_ACCESS,
        MODULE_POKE_ACCESS,
        MODULE_CONTACTS_ACCESS,
        MODULE_PAGE_DASHBOARD_ACCESS,
        MODULE_PAGE_MEMBERS_ACCESS,
        MODULE_PAGE_INVITES_ACCESS,
    }

    ALL = GLOBAL_SCOPED | REALM_SCOPED | ENTITY_TYPE_SCOPED


# Role -> set of realm-scoped permissions granted by default when no
# EntityPermission override exists for that (entity, permission, realm) scope.
# Explicit per-role sets (not computed via inheritance) so each tier's grants
# are visible at a glance during audit/review.
#
# NOTE on owner-exclusivity: REALM_DELETE and REALM_OWNERSHIP_TRANSFER are the
# only permissions exclusive to owner in this flat role->permission matrix.
# "An admin can't remove/demote another admin or the owner" is a separate,
# target-role-aware business rule (an admin CAN remove/demote a plain member
# or moderator) - not expressible as a single actor-role->permission mapping,
# so it must be enforced at the call site as an additional check alongside
# has_permission(entity, Permission.REALM_MEMBER_REMOVE / REALM_MEMBER_ROLE_UPDATE, realm):
# reject if the target member's current role is 'owner', or is 'admin' while
# the acting entity's role is not 'owner'. Same category of thing as
# entity.ownership.assert_owns() - a structural rule, not a grantable permission.
REALM_ROLE_DEFAULT_PERMISSIONS = {
    MemberRole.OWNER: {
        Permission.REALM_UPDATE,
        Permission.REALM_INVITE_CREATE,
        Permission.REALM_INVITE_REVOKE,
        Permission.REALM_REQUEST_APPROVE,
        Permission.REALM_REQUEST_DECLINE,
        Permission.REALM_MEMBER_VIEW,
        Permission.REALM_MEMBER_ADD,
        Permission.REALM_MEMBER_REMOVE,
        Permission.REALM_MEMBER_ROLE_UPDATE,
        Permission.REALM_FOLLOWER_REMOVE,
        Permission.REALM_CHANNEL_CREATE,
        Permission.REALM_MEDIA_UPDATE,
        Permission.REALM_DELETE,
        Permission.REALM_OWNERSHIP_TRANSFER,
    },
    MemberRole.ADMIN: {
        Permission.REALM_UPDATE,
        Permission.REALM_INVITE_CREATE,
        Permission.REALM_INVITE_REVOKE,
        Permission.REALM_REQUEST_APPROVE,
        Permission.REALM_REQUEST_DECLINE,
        Permission.REALM_MEMBER_VIEW,
        Permission.REALM_MEMBER_ADD,
        Permission.REALM_MEMBER_REMOVE,
        Permission.REALM_MEMBER_ROLE_UPDATE,
        Permission.REALM_FOLLOWER_REMOVE,
        Permission.REALM_CHANNEL_CREATE,
        Permission.REALM_MEDIA_UPDATE,
    },
    MemberRole.MODERATOR: {
        Permission.REALM_REQUEST_APPROVE,
        Permission.REALM_REQUEST_DECLINE,
        Permission.REALM_MEMBER_VIEW,
    },
    MemberRole.MEMBER: {
        Permission.REALM_MEMBER_VIEW,
    },
}


def _account_in_good_standing(entity):
    """Platform-default predicate for global-scoped permissions: allowed
    unless explicitly denied. Non-user entities (realm/bot) have no Account
    to check, so they fall through to explicit grant/deny only."""
    account = getattr(entity, "users", None)  # Account's OneToOneField related_name
    if account is None:
        return True
    return account.is_active


# Global permission -> predicate(entity) -> bool. The "allowed unless
# explicitly denied" baseline for ordinary users.
GLOBAL_PLATFORM_DEFAULT = {
    Permission.MESSAGES_SEND: _account_in_good_standing,
    Permission.MESSAGES_REACT: _account_in_good_standing,
    Permission.CONVERSATIONS_CREATE: _account_in_good_standing,
    Permission.POSTS_CREATE: _account_in_good_standing,
    Permission.COMMENTS_CREATE: _account_in_good_standing,
    Permission.CONTACTS_REQUEST_CREATE: _account_in_good_standing,
    Permission.POKE_CREATE: _account_in_good_standing,
    Permission.AI_PROMPT_ASSIST: _account_in_good_standing,
    Permission.WEBRTC_CALL_JOIN: _account_in_good_standing,
    Permission.REALM_SERVER_CREATE: _account_in_good_standing,
}
