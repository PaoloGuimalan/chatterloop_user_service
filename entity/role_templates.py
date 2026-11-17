"""Creation templates only: configured realm roles never inherit later changes."""
from entity.permissions import MemberRole, Permission, REALM_ROLE_DEFAULT_PERMISSIONS

DEFAULT_ROLE_NAMES = {"owner": "Owner", "admin": "Admin", "member": "Member"}


def template_permissions(code, realm_type):
    if code == "owner":
        return set()  # Intrinsic active realm permissions, never a configurable grant list.
    permissions = set(REALM_ROLE_DEFAULT_PERMISSIONS[MemberRole.ADMIN if code == "admin" else MemberRole.MEMBER])
    permissions -= {Permission.REALM_ROLES_MANAGE, Permission.REALM_PERMISSIONS_MANAGE}
    if code == "admin" and realm_type != "community":
        permissions.add(Permission.REALM_IDENTITY_ACT)
    if realm_type in ("group", "server"):
        permissions |= {Permission.REALM_INVITE_CREATE, Permission.REALM_MEMBER_ADD}
    return permissions
