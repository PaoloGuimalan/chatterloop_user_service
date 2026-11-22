"""Lifecycle classification only; none of these predicates grants access."""
from enum import StrEnum


class RealmLifecycleState(StrEnum):
    ACTIVE = 'active'
    DEACTIVATED = 'deactivated'
    RESTRICTED = 'restricted'
    DELETING = 'deleting'


def deletion_pending(realm):
    return realm.deletion_requested_at is not None


def voluntarily_deactivated(realm):
    return not realm.is_active and realm.deactivated_at is not None and not deletion_pending(realm)


def lifecycle_state(realm):
    if deletion_pending(realm):
        return RealmLifecycleState.DELETING
    if realm.is_active:
        return RealmLifecycleState.ACTIVE
    return RealmLifecycleState.DEACTIVATED if voluntarily_deactivated(realm) else RealmLifecycleState.RESTRICTED
