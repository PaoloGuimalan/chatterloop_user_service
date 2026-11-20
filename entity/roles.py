"""Realm-owned role identities and one-time creation templates."""
from django.db import transaction
from django.db.models import Case, CharField, F, Q, Value, When
from django.utils.text import slugify

BUILTIN_ROLE_CODES = frozenset({"owner", "admin", "moderator", "member"})


def generate_role_code(role, *, collision=False):
    if role.is_default:
        return role.code or role.name.strip().lower()
    from entity.models import Role
    stem = (slugify(role.name) or "role")[:245]
    used = set(Role.objects.filter(realm_id=role.realm_id).values_list("code", flat=True))
    code = stem
    suffix = 2
    while code in used or code in BUILTIN_ROLE_CODES:
        code = f"{stem}-{suffix}"
        suffix += 1
    return code


def role_tier(role):
    return role.code if role.is_default and role.code in BUILTIN_ROLE_CODES else "member"


def role_tier_filter(tiers, prefix="role"):
    return Q(**{f"{prefix}__code__in": tiers, f"{prefix}__is_default": True,
                f"{prefix}__realm_id": F("realm_id")})


def role_tier_expression(prefix="role"):
    return Case(When(**{f"{prefix}__is_default": True, f"{prefix}__realm_id": F("realm_id")},
                     then=F(f"{prefix}__code")), default=Value("member"), output_field=CharField())


@transaction.atomic
def ensure_default_roles(realm):
    from community.models import Realm
    from entity.models import PermissionCatalogEntry, Role
    from entity.role_templates import DEFAULT_ROLE_NAMES, template_permissions
    from entity.services.permission_applicability import applicable_permission_codes
    realm = Realm.objects.select_for_update().get(pk=getattr(realm, "pk", realm))
    applicable = applicable_permission_codes(realm)
    result = {}
    for code, name in DEFAULT_ROLE_NAMES.items():
        role, created = Role.objects.get_or_create(realm=realm, code=code, defaults={"name": name, "is_default": True})
        if not role.is_default:
            raise ValueError("A reserved default code has an invalid definition")
        if created and code != "owner":
            role.permissions.set(PermissionCatalogEntry.objects.filter(code__in=template_permissions(code, realm.type) & applicable,
                scope="realm", is_active=True))
        result[code] = role
    return result


def get_default_role(name, *, realm, using="default"):
    from entity.models import Role
    realm_id = getattr(realm, "pk", realm)
    if realm_id is None:
        raise ValueError("Realm context is required")
    try:
        return Role.objects.using(using).get(code=name, is_default=True, realm_id=realm_id)
    except Role.DoesNotExist as exc:
        raise ValueError(f"No default {name} role is configured for this realm") from exc


def get_default_member_role_id():
    """Frozen old-schema callback for community migration 0011 only."""
    from entity.models import Role
    return Role.objects.values_list("pk", flat=True).get(name="member", is_default=True)


def get_member_role_id(realm):
    return get_default_role("member", realm=realm).pk


def resolve_role(role=None, *, role_id=None, allowed_names=None, realm=None):
    from entity.models import Role
    realm_id = getattr(realm, "pk", realm)
    if realm_id is None:
        raise ValueError("Realm context is required")
    if isinstance(role, Role) and role_id is None:
        result = role
    elif role_id is not None or isinstance(role, int) or (isinstance(role, str) and role.isdecimal()):
        value = role_id if role_id is not None else role
        if isinstance(value, bool) or not str(value).isdecimal() or int(value) < 1:
            raise ValueError("Choose a valid role_id")
        try:
            result = Role.objects.get(pk=int(value), realm_id=realm_id)
        except Role.DoesNotExist as exc:
            raise ValueError("The selected role does not belong to this realm") from exc
    else:
        result = get_default_role(str(role or "").strip().lower(), realm=realm_id)
    if str(result.realm_id) != str(realm_id):
        raise ValueError("The selected role belongs to another realm")
    if allowed_names is not None and role_tier(result) not in allowed_names:
        raise ValueError("The selected role cannot be assigned here")
    return result


def realm_owner_role_id(realm):
    return get_default_role("owner", realm=realm).pk
