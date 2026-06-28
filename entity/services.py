"""Helpers for minting and resolving :class:`entity.models.Entity` rows.

Lazy model lookups (``apps.get_model``) keep this module import-safe regardless
of app-loading order, since it is imported from ``entity.signals`` during
``AppConfig.ready``.
"""

from django.apps import apps

from entity.models import Entity, build_entity_id

__all__ = [
    "build_entity_id",
    "parse_entity_id",
    "entity_for_account",
    "entity_for_realm",
    "resolve_entity",
    "resolve_entities",
    "ELIGIBLE_ACT_AS_ROLES",
    "can_act_as_realm",
    "get_active_actor",
]

# Roles that may act on behalf of a realm. Deliberately realm-type-agnostic
# (pages, groups, servers, communities, conferences all use the same gate) and
# kept as a single extension point — a future policy could vary this per
# realm.type without touching call sites.
ELIGIBLE_ACT_AS_ROLES = {"admin", "owner", "creator"}

# Header a client sends to act as a non-self entity.
ACTING_AS_HEADER = "X-Acting-As"


def parse_entity_id(entity_id: str):
    """Split ``entity:<type>:<source_id>`` into ``(entity_type, source_id)``.

    Returns ``(None, None)`` for anything that is not a well-formed entity id.
    ``source_id`` may itself contain colons, so we split with a max of 2.
    """
    if not entity_id or not isinstance(entity_id, str):
        return None, None
    parts = entity_id.split(":", 2)
    if len(parts) != 3 or parts[0] != "entity":
        return None, None
    return parts[1], parts[2]


def entity_for_account(account) -> Entity:
    """Get or create the user Entity backing an Account."""
    entity, _ = Entity.objects.get_or_create(
        source_type=Entity.SOURCE_TYPE_ACCOUNT,
        source_id=str(account.id),
        defaults={
            "entity_type": Entity.ENTITY_TYPE_USER,
            "entity_id": build_entity_id(Entity.ENTITY_TYPE_USER, str(account.id)),
            "account": account,
        },
    )
    return entity


def entity_for_realm(realm) -> Entity:
    """Get or create the realm Entity backing a Realm.

    The canonical source_id is ``realm.realm_id`` (the 15-digit business key),
    not the uuid pk, because the messaging layer (Mongo + Node SQL) keys realm
    conversations on ``realm_id``.
    """
    entity, _ = Entity.objects.get_or_create(
        source_type=Entity.SOURCE_TYPE_REALM,
        source_id=str(realm.realm_id),
        defaults={
            "entity_type": Entity.ENTITY_TYPE_REALM,
            "entity_id": build_entity_id(
                Entity.ENTITY_TYPE_REALM, str(realm.realm_id)
            ),
            "realm": realm,
        },
    )
    return entity


def resolve_entity(entity_id: str):
    """Resolve an entity id to its underlying Account or Realm instance.

    Returns ``None`` if the id is malformed or the source row is missing.
    """
    entity_type, source_id = parse_entity_id(entity_id)
    if entity_type == Entity.ENTITY_TYPE_USER:
        Account = apps.get_model("user", "Account")
        return Account.objects.filter(id=source_id).first()
    if entity_type == Entity.ENTITY_TYPE_REALM:
        Realm = apps.get_model("community", "Realm")
        return Realm.objects.filter(realm_id=source_id).first()
    return None


def resolve_entities(entity_ids):
    """Batch variant of :func:`resolve_entity` to avoid N+1 in list serializers.

    Returns ``{entity_id: Account | Realm}`` for every id that resolves.
    """
    user_ids = []
    realm_ids = []
    for eid in set(filter(None, entity_ids)):
        etype, sid = parse_entity_id(eid)
        if etype == Entity.ENTITY_TYPE_USER:
            user_ids.append(sid)
        elif etype == Entity.ENTITY_TYPE_REALM:
            realm_ids.append(sid)

    resolved = {}
    if user_ids:
        Account = apps.get_model("user", "Account")
        for acc in Account.objects.filter(id__in=user_ids):
            resolved[build_entity_id(Entity.ENTITY_TYPE_USER, str(acc.id))] = acc
    if realm_ids:
        Realm = apps.get_model("community", "Realm")
        for realm in Realm.objects.filter(realm_id__in=realm_ids):
            resolved[
                build_entity_id(Entity.ENTITY_TYPE_REALM, str(realm.realm_id))
            ] = realm
    return resolved


def can_act_as_realm(account, realm) -> bool:
    """Whether ``account`` (a human) may act on behalf of ``realm``.

    Realm-type-agnostic: the realm creator is always eligible; otherwise the
    user must hold an eligible membership role. Works for any realm.type.
    """
    if account is None or realm is None:
        return False
    if str(getattr(realm, "created_by_id", "")) == str(account.id):
        return True
    Member = apps.get_model("community", "Member")
    return Member.objects.filter(
        account=account, realm=realm, role__in=ELIGIBLE_ACT_AS_ROLES
    ).exists()


def get_active_actor(request):
    """Resolve the Entity a request is acting as, validating authorization.

    No header (or the user's own entity) -> the user's entity. A realm entity
    requires the requester to satisfy :func:`can_act_as_realm`. Raises DRF
    ``PermissionDenied`` for any spoofed or unauthorized actor.
    """
    from rest_framework.exceptions import PermissionDenied

    account = getattr(request, "user", None)
    # Unauthenticated request (e.g. AnonymousUser on AllowAny endpoints): no actor.
    if account is None or getattr(account, "id", None) is None:
        return None
    header = request.headers.get(ACTING_AS_HEADER) if hasattr(request, "headers") else None
    if not header:
        return entity_for_account(account)

    entity_type, source_id = parse_entity_id(header)
    if entity_type == Entity.ENTITY_TYPE_USER:
        if source_id != str(account.id):
            raise PermissionDenied("Cannot act as another user.")
        return entity_for_account(account)

    if entity_type == Entity.ENTITY_TYPE_REALM:
        from django.db.models import Q

        Realm = apps.get_model("community", "Realm")
        # The acting-as id may carry the realm's slug (the profile key) or its
        # realm_id; resolve either, then record the canonical realm entity.
        realm = Realm.objects.filter(
            Q(realm_id=source_id) | Q(slug=source_id)
        ).first()
        if realm is None:
            raise PermissionDenied("Unknown realm entity.")
        if not can_act_as_realm(account, realm):
            raise PermissionDenied("Not allowed to act as this realm.")
        return entity_for_realm(realm)

    raise PermissionDenied("Malformed acting-as entity.")
