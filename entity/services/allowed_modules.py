from entity.permissions import Permission
from entity.services.permission_resolver import has_permission
from entity.utils import get_entity_name, get_entity_profile_path


def resolve_allowed_modules_and_context(entity, user):
    """
    Single source of truth for allowed_modules + active_entity, shared by
    MyAllowedModules (the standalone refresh endpoint) and every endpoint
    that builds fresh authentication state (login, register, third-party
    auth) - those endpoints merge this directly into their own response so
    the frontend doesn't need a separate follow-up call just to learn which
    modules to show and whether it's acting as a page.

    Returns (allowed_modules, active_entity) - callers are responsible for
    adding personal_entity_id (always str(user.entity.id)) themselves.
    """
    allowed_modules = [
        codename
        for codename in Permission.ENTITY_TYPE_SCOPED
        if has_permission(entity, codename)
    ]

    is_personal = entity.id == user.entity.id
    if is_personal:
        active_entity = {
            "id": str(entity.id),
            "type": "user",
            "realm_type": None,
            "realm_id": None,
            "name": None,
            "slug": None,
            "profile": user.profile,
        }
    else:
        realm = getattr(entity, "realms", None)
        active_entity = {
            "id": str(entity.id),
            "type": "realm",
            "realm_type": realm.type if realm else None,
            "realm_id": realm.id if realm else None,
            "name": get_entity_name(entity),
            "slug": get_entity_profile_path(entity),
            "profile": realm.profile if realm else None,
        }

    return allowed_modules, active_entity
