"""Block-edge lookups.

Relocated from user/utils/blocking.py alongside the Block model itself. Every
argument here is an Entity, so these work unchanged for pages and realms, not
just personal accounts.
"""

from django.db.models import Q

from entity.models import Block


def get_blocked_account_ids(entity):
    """All entity ids in a block relationship with this entity, either direction."""
    blocked_relations = Block.objects.filter(
        Q(blocker=entity) | Q(blocked=entity)
    ).values_list("blocker_id", "blocked_id")

    blocked_ids = set()
    for blocker_id, blocked_id in blocked_relations:
        blocked_ids.add(blocker_id)
        blocked_ids.add(blocked_id)
    blocked_ids.discard(entity.id)
    return blocked_ids


def is_blocked(entity_q, entity_b):
    return Block.objects.filter(
        Q(blocker=entity_q, blocked=entity_b) | Q(blocker=entity_b, blocked=entity_q)
    ).exists()
