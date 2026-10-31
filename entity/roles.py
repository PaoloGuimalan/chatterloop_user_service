"""Resolve exact role identities; names are only tiers and legacy defaults."""


def get_default_role(name, using="default"):
    from entity.models import Role

    try:
        return Role.objects.using(using).get(name=name, is_default=True)
    except Role.DoesNotExist as exc:
        raise ValueError(f"No default {name} role is configured") from exc


def get_default_member_role_id():
    return get_default_role("member").pk


def resolve_role(role=None, *, role_id=None, allowed_names=None):
    from entity.models import Role

    if isinstance(role, Role) and role_id is None:
        result = role
    elif role_id is not None or isinstance(role, int) or (isinstance(role, str) and role.isdecimal()):
        value = role_id if role_id is not None else role
        if isinstance(value, bool) or not str(value).isdecimal() or int(value) < 1:
            raise ValueError("Choose a valid role_id")
        try:
            result = Role.objects.get(pk=int(value))
        except Role.DoesNotExist as exc:
            raise ValueError("The selected role does not exist") from exc
    else:
        # Compatibility for older clients is tied to an explicit default,
        # never an arbitrary row with a matching (non-unique) name.
        name = str(role or "").strip().lower()
        result = get_default_role(name)
    if allowed_names is not None and result.name not in allowed_names:
        raise ValueError("The selected role cannot be assigned here")
    return result


def realm_owner_role_id(realm):
    from community.models import Member

    role_ids = list(Member.objects.filter(realm=realm, role__name="owner")
                    .values_list("role_id", flat=True).distinct()[:2])
    if role_ids:
        return role_ids[0] if len(role_ids) == 1 else None
    return get_default_role("owner").pk
