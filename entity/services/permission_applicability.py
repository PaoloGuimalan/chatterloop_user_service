"""Realm applicability shared by catalog forms, authorization and grant editing."""
from collections.abc import Mapping


def realm_type_choices():
    # Lazy import: community models also reference the entity catalog.
    from community.models import Realm
    return Realm.REALM_TYPE_CHOICES


def _value(record, key, default=None):
    return record.get(key, default) if isinstance(record, Mapping) else getattr(record, key, default)


def permission_realm_types(entry):
    kinds = _value(entry, 'realm_types')
    if not isinstance(entry, Mapping):
        return [kind.code for kind in kinds.all()] if getattr(entry, 'pk', None) else []
    return kinds


def valid_realm_types(values):
    return isinstance(values, list) and all(
        isinstance(kind, str) and kind in dict(realm_type_choices()) for kind in values
    )


def effective_realm_kind(realm):
    kind = _value(realm, 'type')
    return 'channel' if kind == 'group' and _value(realm, 'parent_id') else kind


def permission_applies(entry, realm):
    """Activity is checked by callers; malformed realm metadata fails closed."""
    if _value(entry, 'scope') != 'realm':
        return True
    kinds = permission_realm_types(entry)
    return bool(valid_realm_types(kinds) and kinds and effective_realm_kind(realm) in kinds)


def applicable_permission_codes(realm, *, catalog=None):
    if catalog is None:
        from entity.models import PermissionCatalogEntry
        return set(PermissionCatalogEntry.objects.filter(scope='realm', is_active=True,
            realm_types__code=effective_realm_kind(realm)).values_list('code', flat=True))
    entries = catalog.items() if isinstance(catalog, Mapping) else (
        (_value(entry, 'code'), entry) for entry in catalog)
    return {code for code, entry in entries if _value(entry, 'scope') == 'realm'
            and _value(entry, 'is_active', True) and permission_applies(entry, realm)}


def sync_applicable_role_grants(role, entries):
    """Replace visible grants, retaining inactive/inapplicable historical links.

    Callers validate submissions and serialize writes on the realm/role rows.
    Recheck here too so administrative form saves cannot add forbidden grants.
    """
    from django.core.exceptions import ValidationError
    from entity.models import PermissionCatalogEntry
    entries = list(entries)
    allowed = applicable_permission_codes(role.realm)
    if any(entry.code not in allowed for entry in entries):
        raise ValidationError('Choose active permissions applicable to this realm only.')
    hidden = role.permissions.exclude(code__in=allowed)
    keep_ids = set(hidden.values_list('pk', flat=True)) | {entry.pk for entry in entries}
    role.permissions.set(PermissionCatalogEntry.objects.filter(pk__in=keep_ids))
