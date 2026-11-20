"""Read-only inspection before the applicability cutover; never repairs data."""
from importlib import import_module

from django.core.management.base import BaseCommand

from community.models import Invite, Realm
from entity.models import PermissionCatalogEntry, RolePermission
from entity.services.permission_applicability import permission_applies, realm_type_choices, permission_realm_types


class Command(BaseCommand):
    help = 'Report invalid realm permission metadata and historical grants without changing records.'

    def handle(self, *args, **options):
        builtins = import_module('entity.migrations.0029_permission_realm_types').REALM_TYPES_BY_CODE
        for realm in Realm.objects.exclude(type__in=dict(realm_type_choices())).order_by('pk'):
            self.stdout.write(f'Unknown realm type: {realm.pk}: {realm.type}')
        for entry in PermissionCatalogEntry.objects.filter(scope='realm').prefetch_related('realm_types').order_by('code'):
            if entry.code not in builtins:
                self.stdout.write(f'Custom permission (review applicability): {entry.code}')
            if entry.is_active and not permission_realm_types(entry):
                self.stdout.write(f'Invalid realm_types: {entry.code}')
        grants = RolePermission.objects.select_related('role__realm', 'permission').prefetch_related('permission__realm_types').order_by('pk')
        for grant in grants.iterator(chunk_size=500):
            if grant.permission.scope == 'realm' and not permission_applies(grant.permission, grant.role.realm):
                self.stdout.write(f'Ineffective grant: role={grant.role_id} permission={grant.permission.code}')
        # Own-page management invitations lack a recoverable personal grantor.
        from django.db.models import F
        ambiguous = Invite.objects.filter(realm__type='page', purpose='manage', status='pending',
                                          created_by_id=F('realm__entity_id')).order_by('pk')
        for invite in ambiguous:
            self.stdout.write(f'Legacy management invite requires revoke/reissue: {invite.pk}')
