"""Current, realm-scoped audiences for management inbox activity."""
from django.db.models import Exists, F, OuterRef, Q, Case, When, Value, CharField
from django.utils.timezone import now

from community.models import Member
from entity.models import EntityPermission, PermissionCatalogEntry, RolePermission
from entity.services.permission_catalog_cache import get_catalog
from entity.services.permission_resolver import has_active_permission


NOTICE_PERMISSIONS = {
    'community_post_pending': ('realm.post.review',),
    'community_invite_pending': ('realm.invite.review',),
    'community_join_request': ('realm.notifications.view', 'realm.request.approve', 'realm.request.decline'),
}
DEFAULT_PERMISSIONS = ('realm.notifications.view',)


def permissions_for_notice(kind):
    return NOTICE_PERMISSIONS.get(kind, DEFAULT_PERMISSIONS)


def permitted_members(realm, codes):
    """One bounded SQL query; no role-name authority or per-member lookups."""
    members = Member.objects.filter(realm=realm, realm__is_active=True, role__realm_id=F('realm_id')).annotate(
        _notice_kind=Case(When(realm__type='group', realm__parent_id__isnull=False, then=Value('channel')),
            default=F('realm__type'), output_field=CharField()))
    eligible = Q(pk__in=[])
    for index, code in enumerate(dict.fromkeys(codes)):
        overrides = EntityPermission.objects.filter(realm=realm, entity_id=OuterRef('entity_id'),
            permission__code=code).filter(Q(expires_at=None) | Q(expires_at__gt=now()))
        names = {suffix: f'_notice_{index}_{suffix}' for suffix in ('active', 'grant', 'deny', 'role')}
        members = members.annotate(**{
            names['active']: Exists(PermissionCatalogEntry.objects.filter(code=code, scope='realm',
                is_active=True, realm_types__code=OuterRef('_notice_kind'))),
            names['grant']: Exists(overrides.filter(effect='grant')),
            names['deny']: Exists(overrides.filter(effect='deny')),
            names['role']: Exists(RolePermission.objects.filter(role_id=OuterRef('role_id'), permission__code=code)),
        })
        eligible |= Q(**{names['active']: True}) & (Q(role__code='owner', role__is_default=True) |
            (Q(**{names['deny']: False}) & (Q(**{names['grant']: True}) | Q(**{names['role']: True}))))
    return members.filter(eligible)


def allowed_notice_types(actor, realm, kinds):
    if actor is None or not realm.is_active or not Member.objects.filter(
        entity=actor, realm=realm, role__realm_id=F('realm_id')).exists():
        return ()
    catalog = get_catalog()
    context = {'catalog': catalog}
    allowed = {}
    for code in {code for kind in kinds for code in permissions_for_notice(kind)}:
        entry = catalog.get(code)
        allowed[code] = bool(entry and entry['is_active'] and entry['scope'] == 'realm' and
            has_active_permission(actor, code, realm, context=context))
    return tuple(kind for kind in kinds if any(allowed[code] for code in permissions_for_notice(kind)))
