"""Staff disabling is a restriction, distinct from voluntary recovery."""
from django import forms

from community.models import Realm


class RealmLifecycleForm(forms.ModelForm):
    class Meta:
        model = Realm
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        if self.instance.deletion_requested_at is not None and cleaned.get('is_active'):
            self.add_error('is_active', 'A realm pending permanent deletion cannot be reactivated.')
        # Staff edits must not make a restriction self-service recoverable.
        self.instance.deactivated_at = None
        cleaned['deactivated_at'] = None
        return cleaned
