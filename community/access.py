"""Current realm/ancestor availability, separate from membership and privacy."""
from dataclasses import dataclass

from django.db import connection
from django.db.models.expressions import RawSQL
from django.db.models import Func, F, BooleanField
from rest_framework.exceptions import PermissionDenied





@dataclass(frozen=True)
class RealmAccessResult:
    allowed: bool
    reason: str | None = None
    root_id: str | None = None


def active_realms(queryset):
    return queryset.alias(_lifecycle_allowed=RealmAvailable(F('parent_id'),F('is_active'),F('deletion_requested_at'))).filter(_lifecycle_allowed=True)


class RealmAvailable(Func):
    """Compile against the current ORM alias, including nested subqueries.

    Roots use selected columns. A child walks only its own ancestors; an
    unrelated realm is never a recursive seed.
    """
    output_field = BooleanField()

    def as_sql(self, compiler, connection, **extra_context):
        compiled=[compiler.compile(value) for value in self.source_expressions]
        parent,active,deleting=[value[0] for value in compiled]
        return f'''({active} = TRUE AND {deleting} IS NULL AND CASE WHEN {parent} IS NULL THEN TRUE ELSE EXISTS(
          WITH RECURSIVE ancestry AS (
            SELECT lifecycle_seed.id,lifecycle_seed.parent_id,lifecycle_seed.is_active,lifecycle_seed.deletion_requested_at
            FROM community_realm lifecycle_seed WHERE lifecycle_seed.id={parent}
            UNION SELECT p.id,p.parent_id,p.is_active,p.deletion_requested_at
            FROM ancestry a JOIN community_realm p ON p.id=a.parent_id WHERE a.parent_id IS NOT NULL
          ) SELECT COUNT(*) FROM ancestry HAVING COUNT(*)>0
            AND SUM(CASE WHEN is_active=TRUE AND deletion_requested_at IS NULL THEN 0 ELSE 1 END)=0
            AND SUM(CASE WHEN parent_id IS NULL THEN 1 ELSE 0 END)>0
        ) END)''', [*compiled[1][1],*compiled[2][1],*compiled[0][1],*compiled[0][1]]


def descendant_realms(realm_id):
    from community.models import Realm
    return Realm.objects.filter(pk__in=RawSQL('''WITH RECURSIVE scope AS (
        SELECT id FROM community_realm WHERE id = %s
        UNION SELECT child.id FROM community_realm child JOIN scope parent ON child.parent_id = parent.id
    ) SELECT id FROM scope''', [realm_id]))


def effective_realm_access(realm, *, context=None):
    from community.models import Realm
    key = ('realm_access', realm.pk)
    if context is not None and key in context:
        return context[key]
    visited = set()
    current = realm
    result = None
    while current is not None:
        if current.pk in visited:
            result = RealmAccessResult(False, 'invalid_hierarchy')
            break
        visited.add(current.pk)
        if not current.is_active or current.deletion_requested_at is not None:
            reason = ('deleting' if current.deletion_requested_at is not None else 'inactive') if current.pk == realm.pk else 'parent_inactive'
            result = RealmAccessResult(False, reason, current.pk)
            break
        if current.parent_id is None:
            result = RealmAccessResult(True, root_id=current.pk)
            break
        current = Realm.objects.filter(pk=current.parent_id).first()
    result = result or RealmAccessResult(False, 'invalid_hierarchy')
    if context is not None:
        context[key] = result
    return result


def lock_realm_scope(realm_id, *, exclusive):
    """Caller owns the transaction; root-first locks serialize lifecycle/writes."""
    from community.models import Realm
    if not connection.in_atomic_block:
        raise RuntimeError('Realm scope locks require an atomic transaction.')
    path, visited, target = [], set(), realm_id
    while target is not None:
        if target in visited:
            raise PermissionDenied('This realm is unavailable.')
        visited.add(target)
        row = Realm.objects.filter(pk=target).first()
        if row is None:
            raise PermissionDenied('This realm is unavailable.')
        path.append(row)
        target = row.parent_id
    path.reverse()
    locked = []
    for expected in path:
        if connection.vendor == 'postgresql' and not exclusive:
            with connection.cursor() as cursor:
                cursor.execute('SELECT id FROM community_realm WHERE id = %s FOR SHARE', [expected.pk])
        row = Realm.objects.select_for_update().get(pk=expected.pk) if exclusive else Realm.objects.get(pk=expected.pk)
        if row.parent_id != expected.parent_id:
            raise PermissionDenied('This realm changed. Please try again.')
        locked.append(row)
    return locked


def lock_active_realm_scopes(realms, *, exclusive_ids=()):
    """Serialize a multi-scope mutation against lifecycle transitions."""
    from community.models import Realm
    if not connection.in_atomic_block:
        raise RuntimeError('Realm scope locks require an atomic transaction.')
    exclusive_ids = set(exclusive_ids)
    scopes, rows = {}, {}
    for realm in realms:
        current, seen, path = realm, set(), []
        while current is not None:
            if current.pk in seen:
                raise PermissionDenied('This realm is unavailable.')
            seen.add(current.pk)
            path.append(current)
            if current.parent_id is None:
                break
            current = Realm.objects.filter(pk=current.parent_id).first()
            if current is None:
                raise PermissionDenied('This realm is unavailable.')
        scopes[realm.pk] = realm
        for depth, expected in enumerate(reversed(path)):
            key = (str(current.pk), depth, str(expected.pk))
            previous = rows.get(expected.pk)
            exclusive = realm.pk in exclusive_ids or bool(previous and previous[2])
            rows[expected.pk] = (key, expected, exclusive)
    for _, expected, exclusive in sorted(rows.values(), key=lambda item: item[0]):
        if exclusive:
            fresh = Realm.objects.select_for_update().get(pk=expected.pk)
        else:
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SELECT id FROM community_realm WHERE id = %s FOR SHARE', [expected.pk])
            fresh = Realm.objects.get(pk=expected.pk)
        if fresh.parent_id != expected.parent_id:
            raise PermissionDenied('This realm changed. Please try again.')
        if not fresh.is_active or fresh.deletion_requested_at is not None:
            raise PermissionDenied('This realm is unavailable.')


def lock_realm_mutation(realm, *entities):
    """Existing membership/invite writes own their transaction and target row."""
    from community.models import Realm
    sources = Realm.objects.filter(entity_id__in=[entity.pk for entity in entities if entity is not None])
    lock_active_realm_scopes([realm, *sources], exclusive_ids=[realm.pk])
    return Realm.objects.get(pk=realm.pk)
