from django.contrib import admin
from .models import (
    Entity,
    PermissionCatalogEntry,
    RolePermission,
    EntityPermission,
    EntityTypeDefaultPermission,
)

admin.site.register(Entity)
admin.site.register(PermissionCatalogEntry)
admin.site.register(RolePermission)
admin.site.register(EntityPermission)
admin.site.register(EntityTypeDefaultPermission)
