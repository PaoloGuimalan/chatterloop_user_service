"""
Whole-table cache for the permission catalog and role-default matrix, backed
by the shared Redis instance (confirmed same host as the Node `server` repo -
see reusables/redis/pubsub.js there). Uses the RAW redis connection via
django_redis.get_redis_connection() - same pattern as
user_service/services/redis.py::RedisPubSubClient - rather than Django's
cache.get()/cache.set(), because django.core.cache mangles keys with a
version prefix (e.g. "permcache:catalog:v1" is actually stored under
":1:permcache:catalog:v1"). Node's plain `redis` client needs to read the
exact same plain key, so the mangling has to be bypassed entirely.

Cached values are stored as plain JSON strings, not pickled, so Node can
deserialize them directly.

Two keys, invalidated explicitly via signals (entity/signals.py) rather than
relying on TTL - the data is tiny (~20 permissions, ~30 role-permission rows)
and admin-edited rarely, so cache-the-whole-thing-and-invalidate-on-write is
the right shape, not per-key TTL churn. The TTL below is a safety net only,
in case a signal-based invalidation is ever missed.

Since this bypasses django_redis's Cache API, IGNORE_EXCEPTIONS (set on
CACHES["default"]["OPTIONS"]) does not apply here - failures are caught
explicitly below, falling back to a direct DB query rather than raising.
"""

import json

from django_redis import get_redis_connection

CATALOG_CACHE_KEY = "permcache:catalog:v1"
ROLE_MATRIX_CACHE_KEY = "permcache:role_matrix:v1"
ENTITY_TYPE_MATRIX_CACHE_KEY = "permcache:entity_type_matrix:v1"
CACHE_TTL_SECONDS = 60 * 60  # safety-net only; invalidation is signal-driven


def _redis_get(key):
    try:
        conn = get_redis_connection("default")
        value = conn.get(key)
        return value.decode() if value is not None else None
    except Exception as err:
        print(f"Permission cache read failed for {key!r}, falling back to DB:", err)
        return None


def _redis_set(key, value):
    try:
        conn = get_redis_connection("default")
        conn.set(key, value, ex=CACHE_TTL_SECONDS)
    except Exception as err:
        print(f"Permission cache write failed for {key!r} (non-fatal):", err)


def _redis_delete(key):
    try:
        get_redis_connection("default").delete(key)
    except Exception as err:
        print(f"Permission cache invalidation failed for {key!r} (non-fatal):", err)


def get_catalog():
    """codename -> {"scope": ..., "is_active": ...} for every permission,
    active and inactive (inactive entries must stay present so an unknown
    permission and a disabled one remain distinguishable to callers)."""
    cached = _redis_get(CATALOG_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)

    from entity.models import PermissionCatalogEntry

    catalog = {
        entry.codename: {"scope": entry.scope, "is_active": entry.is_active}
        for entry in PermissionCatalogEntry.objects.all()
    }
    _redis_set(CATALOG_CACHE_KEY, json.dumps(catalog))
    return catalog


def get_role_matrix():
    """role -> [codename, ...], joined against active permissions only."""
    cached = _redis_get(ROLE_MATRIX_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)

    from entity.models import RolePermission

    matrix = {}
    role_permissions = RolePermission.objects.filter(
        permission__is_active=True
    ).select_related("permission")
    for rp in role_permissions:
        matrix.setdefault(rp.role, []).append(rp.permission.codename)

    _redis_set(ROLE_MATRIX_CACHE_KEY, json.dumps(matrix))
    return matrix


def get_entity_type_matrix():
    """entity_type -> [codename, ...], joined against active permissions only."""
    cached = _redis_get(ENTITY_TYPE_MATRIX_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)

    from entity.models import EntityTypeDefaultPermission

    matrix = {}
    entity_type_permissions = EntityTypeDefaultPermission.objects.filter(
        permission__is_active=True
    ).select_related("permission")
    for etp in entity_type_permissions:
        matrix.setdefault(etp.entity_type, []).append(etp.permission.codename)

    _redis_set(ENTITY_TYPE_MATRIX_CACHE_KEY, json.dumps(matrix))
    return matrix


def invalidate_catalog_cache():
    _redis_delete(CATALOG_CACHE_KEY)


def invalidate_role_matrix_cache():
    _redis_delete(ROLE_MATRIX_CACHE_KEY)


def invalidate_entity_type_matrix_cache():
    _redis_delete(ENTITY_TYPE_MATRIX_CACHE_KEY)
