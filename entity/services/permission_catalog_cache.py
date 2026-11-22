"""Shared plain-JSON Redis caches used by Django and Node.

Catalog and entity-type defaults are small global catalogs; exact role grants
are cached per realm. Role grants include inactive codes because every resolver
checks catalog scope/activity first. Signals invalidate immediately and again
at commit so another service's pre-commit cache write cannot survive a mutation.
Redis failures fall back to SQL, and the one-hour TTL is a safety net.
"""

import json

from django_redis import get_redis_connection
import logging

logger = logging.getLogger(__name__)

CATALOG_CACHE_KEY = "permcache:catalog:v5"
ROLE_MATRIX_CACHE_KEY = "permcache:role_matrix:v3"
ENTITY_TYPE_MATRIX_CACHE_KEY = "permcache:entity_type_matrix:v1"
CACHE_TTL_SECONDS = 60 * 60  # safety-net only; invalidation is signal-driven


def _redis_get(key):
    try:
        conn = get_redis_connection("default")
        value = conn.get(key)
        return value.decode() if value is not None else None
    except Exception as err:
        logger.warning(
            "Permission cache read failed for %r, falling back to DB", key,
            exc_info=True,
        )
        return None


def _redis_set(key, value):
    try:
        conn = get_redis_connection("default")
        conn.set(key, value, ex=CACHE_TTL_SECONDS)
    except Exception as err:
        logger.warning(
            "Permission cache write failed for %r (non-fatal)", key,
            exc_info=True,
        )


def _redis_delete(key):
    try:
        get_redis_connection("default").delete(key)
    except Exception as err:
        logger.warning(
            "Permission cache invalidation failed for %r (non-fatal)", key,
            exc_info=True,
        )


def get_catalog():
    """code -> {scope, is_active, realm_types} for every permission,
    active and inactive (inactive entries must stay present so an unknown
    permission and a disabled one remain distinguishable to callers)."""
    cached = _redis_get(CATALOG_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)

    from entity.models import PermissionCatalogEntry
    from entity.services.permission_applicability import permission_realm_types

    catalog = {
        entry.code: {"scope": entry.scope, "is_active": entry.is_active, "realm_types": permission_realm_types(entry)}
        for entry in PermissionCatalogEntry.objects.prefetch_related('realm_types').all()
    }
    _redis_set(CATALOG_CACHE_KEY, json.dumps(catalog))
    return catalog


def get_role_matrix(realm_id):
    """Exact role IDs and raw grants for one realm.

    Catalog activity/scope is checked by the resolver before using these grants,
    so toggling a catalog entry does not require scanning every realm's cache.
    """
    key = f"{ROLE_MATRIX_CACHE_KEY}:{realm_id}"
    cached = _redis_get(key)
    if cached is not None:
        return json.loads(cached)

    from entity.models import RolePermission

    matrix = {}
    role_permissions = RolePermission.objects.filter(
        role__realm_id=realm_id
    ).values_list("role_id", "permission__code")
    for role_id, code in role_permissions:
        matrix.setdefault(str(role_id), []).append(code)

    _redis_set(key, json.dumps(matrix))
    return matrix


def get_entity_type_matrix():
    """entity_type -> [code, ...], joined against active permissions only."""
    cached = _redis_get(ENTITY_TYPE_MATRIX_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)

    from entity.models import PermissionEntityType

    matrix = {}
    entity_type_permissions = PermissionEntityType.objects.filter(
        permission__is_active=True
    ).values_list("entity_type__name", "permission__code")
    for entity_type, code in entity_type_permissions:
        matrix.setdefault(entity_type, []).append(code)

    _redis_set(ENTITY_TYPE_MATRIX_CACHE_KEY, json.dumps(matrix))
    return matrix


def invalidate_catalog_cache():
    _redis_delete(CATALOG_CACHE_KEY)


def invalidate_role_matrix_cache(realm_id):
    _redis_delete(f"{ROLE_MATRIX_CACHE_KEY}:{realm_id}")


def invalidate_entity_type_matrix_cache():
    _redis_delete(ENTITY_TYPE_MATRIX_CACHE_KEY)
