from django.db.models import Q

from ..models import Block


def get_blocked_account_ids(account):
    """All account ids in a block relationship with this account, either direction."""
    blocked_relations = Block.objects.filter(
        Q(blocker=account) | Q(blocked=account)
    ).values_list("blocker_id", "blocked_id")

    blocked_ids = set()
    for blocker_id, blocked_id in blocked_relations:
        blocked_ids.add(blocker_id)
        blocked_ids.add(blocked_id)
    blocked_ids.discard(account.id)
    return blocked_ids


def is_blocked(account_a, account_b):
    return Block.objects.filter(
        Q(blocker=account_a, blocked=account_b)
        | Q(blocker=account_b, blocked=account_a)
    ).exists()
