from django.db.models import Q
from django.utils.timezone import now

from entity.permissions import PermissionEffect
from interests.models import EntityInterest, Interest, MAX_INTEREST_DEPTH
from interests.services.affinity import get_affinity_score

# Tuned as "half of a strong signal" - mirrors the NEW_CONNECTION weight
# (10.0) in newsfeed's INTERACTION_WEIGHTS table used for affinity bumps.
IMPLICIT_GRANT_THRESHOLD = 5.0


def _fetch_with_ancestors(interest):
    """One query, not N sequential .parent hops - chains select_related
    paths up to MAX_INTEREST_DEPTH so the whole (shallow) ancestor chain
    comes back in a single round trip."""
    paths = []
    path = "parent"
    for _ in range(MAX_INTEREST_DEPTH):
        paths.append(path)
        path += "__parent"
    return Interest.objects.select_related(*paths).get(pk=interest.pk)


def _ancestor_chain(interest):
    node = _fetch_with_ancestors(interest)
    chain = []
    for _ in range(MAX_INTEREST_DEPTH):
        if node is None:
            break
        chain.append(node)
        node = node.parent
    return chain


def _active_override(entity, interest):
    return (
        EntityInterest.objects.filter(entity=entity, interest=interest)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now()))
        .first()
    )


def resolve_interest(entity, interest):
    """
    Most-specific-override-wins: walks interest -> parent -> parent.parent...
    and returns the FIRST EntityInterest override found at any level,
    closest to the queried interest first. A specific grant on "hiking"
    beats a broader deny on "Outdoors" - a DELIBERATE departure from
    entity.services.permission_resolver.has_permission()'s "deny always
    beats grant": permissions have no specificity axis to weigh, interests
    do (that's the whole point of the hierarchy - a narrow, explicit opt-in
    is a stronger signal than a broad opt-out).

    Falls through to the implicit EntityInterestAffinity score only when no
    override exists anywhere in the ancestor chain. The implicit signal can
    only ever produce "grant" or "neutral" - never "deny" - so low/no
    engagement can't actively suppress content the way an explicit deny can.

    Returns a tuple: (effect, matched_interest) where effect is
    "grant" | "deny" | "neutral", and matched_interest is the Interest the
    override was found on (None if resolved implicitly or neutral).
    """
    if entity is None or interest is None:
        return ("neutral", None)

    for node in _ancestor_chain(interest):
        override = _active_override(entity, node)
        if override is not None:
            return (override.effect, node)

    if get_affinity_score(entity, interest) >= IMPLICIT_GRANT_THRESHOLD:
        return ("grant", None)
    return ("neutral", None)


def ensure_grant_override(entity_id, interest_ids):
    """
    Explicitly tagging your own diary entry with an interest is a stronger,
    deliberate declaration than an implicit engagement signal (a like/view/
    comment on someone else's content) - "this is my interest", not just "I
    was exposed to this". So diary tagging creates a real
    EntityInterest(effect=grant) row (same shape as one created through
    EntityInterestOverrideView), on top of the EntityInterestAffinity bump
    from bump_interest_affinity() - callers use both together.

    get_or_create, not update_or_create: never overwrites an existing
    override. If the entity already explicitly denied this interest
    elsewhere, re-tagging a diary entry with it doesn't silently flip that
    back to a grant.
    """
    for interest_id in set(interest_ids):
        EntityInterest.objects.get_or_create(
            entity_id=entity_id,
            interest_id=interest_id,
            defaults={"effect": PermissionEffect.GRANT, "created_by_id": entity_id},
        )
