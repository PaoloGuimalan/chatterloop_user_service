from django.contrib import admin

from .models import Entity


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = (
        "entity_id",
        "entity_type",
        "source_type",
        "source_id",
        "created_at",
        "updated_at",
    )
    list_filter = ("entity_type", "source_type")
    search_fields = ("entity_id", "entity_type", "source_type", "source_id")
