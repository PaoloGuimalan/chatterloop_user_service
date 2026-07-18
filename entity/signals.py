from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from entity.models import (
    EntityTypeDefaultPermission,
    PermissionCatalogEntry,
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
    # A permission's scope/is_active can change which role/entity-type
    # grants are valid, so both joined matrix caches (filtered on
    # permission__is_active) must invalidate too.
    invalidate_role_matrix_cache()
    invalidate_entity_type_matrix_cache()


@receiver(post_save, sender=RolePermission)
@receiver(post_delete, sender=RolePermission)
def _on_role_permission_changed(sender, **kwargs):
    invalidate_role_matrix_cache()


@receiver(post_save, sender=EntityTypeDefaultPermission)
@receiver(post_delete, sender=EntityTypeDefaultPermission)
def _on_entity_type_default_permission_changed(sender, **kwargs):
    invalidate_entity_type_matrix_cache()
