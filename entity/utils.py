def get_entity_display_name(entity):
    """
    Human-readable, ready-to-use mention string for whichever concrete
    object this Entity backs - matches how each type is already displayed
    elsewhere in the app (usernames get an "@" prefix, realm/page names
    don't). Needed because request.entity can now be either a personal
    Account or a Realm (entity switching) wherever notification text used
    to assume it was always the logged-in Account.
    """
    account = getattr(entity, "users", None)
    if account is not None:
        return f"@{account.username}"
    realm = getattr(entity, "realms", None)
    if realm is not None:
        return f"@{realm.slug}"
    return str(entity.id)


def get_entity_profile_path(entity):
    """
    URL path segment for this entity's public profile - the Account's
    username for a user, or the Realm's slug (falling back to its realm_id)
    for a page. Distinct from get_entity_display_name(): a realm's display
    name and its URL slug are different values (e.g. "Neon Systems" vs.
    a slug like "neon-systems"), whereas a user's username happens to serve
    both roles.
    """
    account = getattr(entity, "users", None)
    if account is not None:
        return account.username
    realm = getattr(entity, "realms", None)
    if realm is not None:
        return realm.slug or realm.realm_id
    return str(entity.id)
