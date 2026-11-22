"""Current account authority, distinct from the active entity's capabilities."""
from django.apps import apps
from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.utils.timezone import now


def acting_memberships(entity):
    """Exact scoped memberships with explicit authority to use that identity."""
    from entity.models import EntityPermission, RolePermission, PermissionCatalogEntry
    Member = apps.get_model("community", "Member")
    Realm = apps.get_model('community', 'Realm')
    from community.access import active_realms
    entity_id = getattr(entity, "pk", entity)
    overrides = EntityPermission.objects.filter(entity_id=entity_id,
        realm_id=OuterRef("realm_id"), permission__code="realm.identity.act",
        permission__scope="realm", permission__is_active=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now())).order_by("pk")
    grants = RolePermission.objects.filter(role_id=OuterRef("role_id"),
        permission__code="realm.identity.act", permission__scope="realm", permission__is_active=True)
    return Member.objects.filter(entity_id=entity_id, role__realm_id=F("realm_id"),
        realm__is_active=True, realm__type='page', realm_id__in=active_realms(Realm.objects.all()).values('pk')
    ).annotate(_act_active=Exists(PermissionCatalogEntry.objects.filter(code="realm.identity.act", scope="realm", is_active=True, realm_types__code='page')), _act_override=Subquery(overrides.values("effect")[:1]), _act_grant=Exists(grants)).filter(_act_active=True).filter(
        Q(role__is_default=True, role__code="owner") | Q(_act_override="grant") |
        (Q(_act_override__isnull=True) & Q(_act_grant=True)))


def can_act_as_entity(account, entity) -> bool:
    personal_id = getattr(account, "entity_id", None)
    if personal_id is None or entity is None:
        return False
    if str(personal_id) == str(entity.id):
        return True
    return getattr(entity, "type", None) == "realm" and acting_memberships(personal_id).filter(
        realm__entity_id=entity.id).exists()


def is_switchable_realm(realm) -> bool:
    return bool(realm.is_active and realm.type == 'page')
