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
