from __future__ import annotations

from entity.models import Entity
from user.models import Account


def resolve_user_entity(account: Account | str) -> Entity:
    account_id = str(account.id) if isinstance(account, Account) else str(account)
    entity, _ = Entity.get_or_create_from_source(
        entity_type="user",
        source_type="user.account",
        source_id=account_id,
    )
    return entity


def resolve_user_entity_id(account: Account | str) -> str:
    return str(resolve_user_entity(account).id)


def resolve_account_from_entity(entity: Entity | None) -> Account | None:
    if not entity or entity.source_type != "user.account" or not entity.source_id:
        return None
    try:
        return Account.objects.get(id=entity.source_id)
    except Account.DoesNotExist:
        return None
