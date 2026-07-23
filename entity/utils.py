def get_entity_display_username(entity):
    """
    Human-readable, ready-to-use mention string for whichever concrete
    object this Entity backs - both a user's username and a realm's slug
    get an "@" prefix (e.g. "@testuser123", "@neon-systems"). Needed because
    request.entity can now be either a personal Account or a Realm (entity
    switching) wherever notification text used to assume it was always the
    logged-in Account.

    For the entity's actual human-readable name (no "@", e.g. "Neon
    Systems" rather than "@neon-systems"), use get_entity_name() instead -
    this function is specifically the @mention form used in notification/
    activity text, not a general-purpose display name.
    """
    account = getattr(entity, "users", None)
    if account is not None:
        return f"@{account.username}"
    realm = getattr(entity, "realms", None)
    if realm is not None:
        return f"@{realm.slug}"
    return str(entity.id)


def get_entity_name(entity):
    """
    Actual human-readable name for whichever concrete object this Entity
    backs - no "@" mention formatting (e.g. "Neon Systems", not
    "@neon-systems"). For a user this is their first/last name, not their
    username. Use this where an entity's real name needs to be shown (e.g.
    UI headers), and get_entity_display_username() for @mention-style
    notification/activity text.
    """
    account = getattr(entity, "users", None)
    if account is not None:
        return f"{account.first_name} {account.last_name}"
    realm = getattr(entity, "realms", None)
    if realm is not None:
        return realm.name
    return str(entity.id)


def get_entity_profile_path(entity):
    """
    URL path segment for this entity's public profile - the Account's
    username for a user, or the Realm's slug (falling back to its realm_id)
    for a page. Distinct from get_entity_name()/get_entity_display_username():
    a realm's display name and its URL slug are different values (e.g.
    "Neon Systems" vs. a slug like "neon-systems"), whereas a user's
    username happens to serve both roles.
    """
    account = getattr(entity, "users", None)
    if account is not None:
        return account.username
    realm = getattr(entity, "realms", None)
    if realm is not None:
        return realm.slug or realm.realm_id
    return str(entity.id)


def resolve_entity_target(raw_id):
    """
    Resolve a raw client-supplied id to an Entity.

    Accepts an Account id, a Realm id, or a bare entity id, so entity<->entity
    features (contacts, follows) can take either kind of target without the
    client having to declare which it is - and without changing the request
    shape clients already send. Returns None when nothing matches.

    Imports are deferred: this module is imported by user.views, so importing
    the models at module scope would close an import cycle.
    """
    import uuid

    from django.db.models import Q

    from user.models import Account
    from community.models import Realm
    from entity.models import Entity

    if not raw_id:
        return None

    # Account ids are uuids; a realm id is a 15-digit string, so uuid() raises
    # rather than matching. Tried first because it is the common path.
    try:
        return Account.objects.get(id=uuid.UUID(str(raw_id))).entity
    except (ValueError, TypeError, AttributeError, Account.DoesNotExist):
        pass

    realm = Realm.objects.filter(Q(realm_id=raw_id) | Q(id=raw_id)).first()
    if realm:
        return realm.entity

    return Entity.objects.filter(id=raw_id).first()


def entity_side_is_visible(field):
    """
    Q predicate for "the entity on `field` is a usable counterpart": an
    active + verified user account, OR an active realm.

    Connections and follows are entity<->entity, so the old user-only form
    (`{field}__users__is_active=True, ...`) silently dropped every row whose
    side was a page - and returned nothing at all when the ACTING entity was
    a page, since its own side could never match. Realms are deliberately not
    required to be is_verified: for a realm that flag is a badge, not an
    access gate (unlike accounts, where it gates email confirmation).

    Usage:
        Connection.objects.filter(
            entity_side_is_visible("action_by"),
            entity_side_is_visible("involved_entity"),
            ...
        )
    """
    from django.db.models import Q

    return Q(
        **{
            f"{field}__users__is_active": True,
            f"{field}__users__is_verified": True,
        }
    ) | Q(**{f"{field}__realms__is_active": True})
