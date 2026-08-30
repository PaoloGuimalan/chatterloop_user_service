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
    # A bot is the third concrete kind an Entity can back. Added last so the
    # two existing branches are reached exactly as before.
    bot = getattr(entity, "bots", None)
    if bot is not None:
        return f"@{bot.handle}"
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
    bot = getattr(entity, "bots", None)
    if bot is not None:
        return bot.name
    return str(entity.id)


def get_entity_profile_picture(entity):
    """
    URL of this entity's profile picture, or None when it has none.

    Both concrete types store it on a `profile` column but disagree on the
    sentinel for "no picture" - an Account defaults to "none" and a Realm to
    "N/A" - so a caller that tests only one of them ends up rendering the
    other as an image URL and getting a broken image. Both are treated as
    absent here.

    Returns None rather than either sentinel: this is for callers building
    fresh payloads, where a null is unambiguous. EmbeddedRealmSerializer
    normalises to the "none" string instead, because existing clients already
    test for that and changing it would break them.
    """
    account = getattr(entity, "users", None)
    if account is not None:
        profile = account.profile
    else:
        realm = getattr(entity, "realms", None)
        bot = getattr(entity, "bots", None)
        if realm is not None:
            profile = realm.profile
        elif bot is not None:
            profile = bot.profile
        else:
            return None

    if not profile or profile in ("N/A", "none"):
        return None
    return profile


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


def mutual_count_subquery(entity, outer_field="entity_id"):
    """
    Subquery counting mutual connections between `entity` and each outer row
    - i.e. "N mutual" on a person card.

    Connections are stored as TWO mirrored rows, so one direction per hop is
    enough: rows where I am action_by give my counterparts F, and F's
    `connections_as_involved_entity` rows with action_by = the candidate
    prove the F<->candidate edge. `.values("action_by")` collapses the whole
    thing to a single COUNT row so this stays one scalar subquery rather
    than a join that fans out the outer query.

    `outer_field` is the OuterRef target holding the candidate's ENTITY id -
    "entity_id" when the outer query is over Account, but a Connection/Follow
    query points at its own entity column instead.

    Shared by the Search page's people cards (entity/search_views.py) and the
    Contacts page's network sections (entity/network_views.py) - it is subtle
    enough that two copies would drift.
    """
    from django.db.models import Count, OuterRef

    from entity.models import Connection

    return (
        Connection.objects.filter(
            action_by=entity,
            status=True,
            involved_entity__connections_as_involved_entity__action_by=OuterRef(
                outer_field
            ),
            involved_entity__connections_as_involved_entity__status=True,
        )
        .exclude(involved_entity=entity)
        .order_by()
        .values("action_by")
        .annotate(n=Count("involved_entity", distinct=True))
        .values("n")[:1]
    )
