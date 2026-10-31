from django import forms

from entity.models import EntityTypeDefinition, PermissionCatalogEntry, Role


class PermissionCatalogForm(forms.ModelForm):
    entity_types = forms.ModelMultipleChoiceField(
        queryset=EntityTypeDefinition.objects.all(), required=False,
    )

    class Meta:
        model = PermissionCatalogEntry
        fields = "__all__"


class RoleForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=PermissionCatalogEntry.objects.all(), required=False,
    )

    class Meta:
        model = Role
        fields = ("name", "is_default", "permissions")
