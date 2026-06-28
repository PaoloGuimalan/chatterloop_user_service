from django.db.models import Q

from ..models import Block
from .entity import resolve_user_entity


def get_blocked_account_ids(account):
    """All account ids in a block relationship with this account, either direction."""
    account_entity = resolve_user_entity(account)
    blocked_relations = Block.objects.filter(
        Q(blocker=account_entity) | Q(blocked=account_entity)
    ).values_list("blocker__source_id", "blocked__source_id")

    blocked_ids = set()
    for blocker_id, blocked_id in blocked_relations:
        if blocker_id:
            blocked_ids.add(str(blocker_id))
        if blocked_id:
            blocked_ids.add(str(blocked_id))
    blocked_ids.discard(str(account.id))
    return blocked_ids


def is_blocked(account_a, account_b):
    account_a_entity = resolve_user_entity(account_a)
    account_b_entity = resolve_user_entity(account_b)
    return Block.objects.filter(
        Q(blocker=account_a_entity, blocked=account_b_entity)
        | Q(blocker=account_b_entity, blocked=account_a_entity)
    ).exists()
