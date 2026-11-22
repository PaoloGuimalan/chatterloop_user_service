from django.contrib import admin
from .models import Realm, Member, Invite, Type
from .lifecycle_forms import RealmLifecycleForm


@admin.register(Realm)
class RealmAdmin(admin.ModelAdmin):
    form = RealmLifecycleForm
    list_display = ('name', 'type', 'is_active', 'deactivated_at', 'deletion_requested_at')
    readonly_fields = ('deactivated_at', 'deletion_requested_at')

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        # ModelAdmin wraps POST change forms in a transaction. Lock before
        # loading/validating the instance so its later full save cannot erase
        # a lifecycle transition accepted by another connection.
        return queryset.select_for_update() if request.method == 'POST' else queryset


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
