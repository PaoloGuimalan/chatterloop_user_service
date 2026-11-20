from django.contrib import admin
from .models import Realm, Member, Invite, Type

admin.site.register(Realm)
admin.site.register(Member)
admin.site.register(Invite)


@admin.register(Type)
class TypeAdmin(admin.ModelAdmin):
    list_display = ('label', 'code')
    readonly_fields = ('code',)
    search_fields = ('code',)

    @admin.display(description='Realm type')
    def label(self, obj):
        return str(obj)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
