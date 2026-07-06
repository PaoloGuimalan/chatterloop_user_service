from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from entity.models import PermissionCatalogEntry, RolePermission
from entity.services.permission_catalog_cache import (
    invalidate_catalog_cache,
    invalidate_role_matrix_cache,
)


@receiver(post_save, sender=PermissionCatalogEntry)
@receiver(post_delete, sender=PermissionCatalogEntry)
def _on_permission_catalog_entry_changed(sender, **kwargs):
    invalidate_catalog_cache()
    # A permission's scope/is_active can change which role grants are valid,
    # so the joined role-matrix cache (filtered on permission__is_active)
    # must invalidate too.
    invalidate_role_matrix_cache()


@receiver(post_save, sender=RolePermission)
@receiver(post_delete, sender=RolePermission)
def _on_role_permission_changed(sender, **kwargs):
    invalidate_role_matrix_cache()
