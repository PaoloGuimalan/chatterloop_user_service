from django.db.models.signals import m2m_changed, post_save, post_delete
from django.dispatch import receiver
from django.db import transaction
from django.core.exceptions import ValidationError

from entity.models import (
    EntityTypeDefinition,
    PermissionEntityType,
    PermissionCatalogEntry,
    Role,
    RolePermission,
)
from entity.services.permission_catalog_cache import (
    invalidate_catalog_cache,
    invalidate_entity_type_matrix_cache,
    invalidate_role_matrix_cache,
)


@receiver(post_save, sender=PermissionCatalogEntry)
@receiver(post_delete, sender=PermissionCatalogEntry)
def _on_permission_catalog_entry_changed(sender, **kwargs):
    invalidate_catalog_cache()
    # Role matrices contain raw grants; the catalog gates activity/scope.
    invalidate_entity_type_matrix_cache()
    transaction.on_commit(invalidate_catalog_cache)
    transaction.on_commit(invalidate_entity_type_matrix_cache)


@receiver(post_save, sender=RolePermission)
@receiver(post_delete, sender=RolePermission)
@receiver(post_save, sender=Role)
@receiver(post_delete, sender=Role)
def _on_role_permission_changed(sender, instance, **kwargs):
    realm_id = instance.realm_id if sender is Role else Role.objects.filter(
        pk=instance.role_id,
    ).values_list("realm_id", flat=True).first()
    if realm_id is not None:
        _invalidate_role_realms({realm_id})


def _invalidate_role_realms(realm_ids):
    for realm_id in realm_ids:
        invalidate_role_matrix_cache(realm_id)
        transaction.on_commit(lambda rid=realm_id: invalidate_role_matrix_cache(rid))


@receiver(m2m_changed, sender=Role.permissions.through)
def _on_role_permissions_changed(sender, action, instance, reverse, pk_set, **kwargs):
    if action in ("pre_add", "pre_remove", "pre_clear"):
        if reverse:
            owners = Role.objects.filter(code="owner", is_default=True)
            protected = owners.filter(pk__in=pk_set).exists() if pk_set is not None else owners.filter(permissions=instance).exists()
        else:
            protected = instance.code == "owner" and instance.is_default
        if protected:
            raise ValidationError("Owner permissions are intrinsic and cannot be changed.")
        # Reverse clear has no pk_set after deleting the relation rows.
        if reverse and action == "pre_clear":
            instance._role_grant_realms = set(Role.objects.filter(
                permissions=instance,
            ).values_list("realm_id", flat=True))
    if action in ("post_add", "post_remove", "post_clear"):
        if not reverse:
            realms = {instance.realm_id}
        elif action == "post_clear":
            realms = getattr(instance, "_role_grant_realms", set())
        else:
            realms = set(Role.objects.filter(pk__in=pk_set).values_list("realm_id", flat=True))
        _invalidate_role_realms(realms)


@receiver(post_save, sender=PermissionEntityType)
@receiver(post_delete, sender=PermissionEntityType)
@receiver(post_save, sender=EntityTypeDefinition)
@receiver(post_delete, sender=EntityTypeDefinition)
def _on_entity_type_default_permission_changed(sender, **kwargs):
    invalidate_entity_type_matrix_cache()


@receiver(m2m_changed, sender=PermissionCatalogEntry.entity_types.through)
def _on_catalog_entity_types_changed(sender, action, **kwargs):
    if action in ("post_add", "post_remove", "post_clear"):
        invalidate_entity_type_matrix_cache()
