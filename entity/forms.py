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
        fields = ("realm", "name", "permissions")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["realm"].disabled = True
            if self.instance.code == "owner":
                self.fields["name"].disabled = True
                self.fields["permissions"].disabled = True
                self.fields["permissions"].help_text = "Owner automatically holds every active realm permission."
