from django.contrib import admin

from bot.models import Bot


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "handle",
        "is_system",
        "is_verified",
        "is_active",
        "created_at",
    )
    list_filter = ("is_system", "is_verified", "is_active")
    search_fields = ("name", "handle", "entity__id")
