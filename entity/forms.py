from django import forms

from entity.models import EntityTypeDefinition, PermissionCatalogEntry, Role
from community.models import Type


class PermissionCatalogForm(forms.ModelForm):
    realm_types = forms.ModelMultipleChoiceField(queryset=Type.objects.all(), to_field_name='code', required=False,
        help_text='Applies only to realm-scoped permissions. Select every supported realm type.')
    entity_types = forms.ModelMultipleChoiceField(
        queryset=EntityTypeDefinition.objects.all(), required=False,
    )

    class Meta:
        model = PermissionCatalogEntry
        fields = "__all__"

    def clean(self):
        data = super().clean()
        if data.get('scope') == 'realm' and data.get('is_active') and not data.get('realm_types'):
            self.add_error('realm_types', 'Select at least one realm type for an active realm permission.')
        return data


class RoleForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=PermissionCatalogEntry.objects.all(), required=False,
    )

    class Meta:
        model = Role
        fields = ("realm", "name", "permissions")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["realm"].disabled = True
            if self.instance.code == "owner":
                self.fields["name"].disabled = True
                self.fields["permissions"].disabled = True
                self.fields["permissions"].help_text = "Owner automatically holds every active realm permission."
        from community.models import Realm
        from entity.services.permission_applicability import applicable_permission_codes
        realm = self.instance.realm if self.instance.realm_id else None
        if realm is None and self.is_bound:
            realm = Realm.objects.filter(pk=self.data.get('realm')).first()
        self.fields['permissions'].queryset = PermissionCatalogEntry.objects.filter(
            code__in=applicable_permission_codes(realm)) if realm else PermissionCatalogEntry.objects.none()
        if self.instance.pk:
            self.initial['permissions'] = list(self.instance.permissions.filter(
                pk__in=self.fields['permissions'].queryset).values_list('pk', flat=True))

    def _save_m2m(self):
        from entity.services.permission_applicability import sync_applicable_role_grants
        if self.instance.code != 'owner' and 'permissions' in self.cleaned_data:
            sync_applicable_role_grants(self.instance, self.cleaned_data['permissions'])
