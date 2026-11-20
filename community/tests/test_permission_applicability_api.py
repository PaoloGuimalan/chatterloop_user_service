from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Member, Realm
from community.permission_views import RealmPermissionsView
from entity.models import Entity, EntityPermission, PermissionCatalogEntry, Role
from entity.tests.permission_fixtures import create_permission
from community.models import Type
from entity.services.permission_resolver import has_permission


class PermissionApplicabilityApiTests(TestCase):
    def setUp(self):
        cache = {}
        for key, effect in [('_redis_get', cache.get), ('_redis_set', cache.__setitem__),
                            ('_redis_delete', lambda key: cache.pop(key, None))]:
            self.enterContext(patch('entity.services.permission_catalog_cache.' + key, side_effect=effect))
        from importlib import import_module
        matrix = import_module('entity.migrations.0029_permission_realm_types').REALM_TYPES_BY_CODE
        for code, kinds in matrix.items():
            if not PermissionCatalogEntry.objects.filter(code=code).exists():
                create_permission(code=code, scope='realm', realm_types=kinds)
        self.actor = Entity.objects.create(type='user')
        self.realm = Realm.objects.create(type='page', name='Page', created_by=self.actor,
                                         entity=Entity.objects.create(type='realm'))
        self.roles = {r.code: r for r in self.realm.owned_roles.all()}
        Member.objects.create(realm=self.realm, entity=self.actor, added_by=self.actor, role=self.roles['owner'])

    def request(self, method='get', data=None, actor=None, personal=None):
        request = getattr(APIRequestFactory(), method)('/permissions', data=data or {}, format='json')
        request.entity = actor or self.actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, entity=personal or self.actor,
                                                        entity_id=(personal or self.actor).pk))
        return RealmPermissionsView.as_view()(request, realm_id=self.realm.pk)

    def test_owner_self_and_override_cannot_authorize_wrong_kind(self):
        permission = PermissionCatalogEntry.objects.get(code='realm.post.submit')
        EntityPermission.objects.create(entity=self.actor, realm=self.realm, permission=permission, effect='grant')
        for entity in (self.actor, self.realm.entity):
            self.assertFalse(has_permission(entity, permission.code, self.realm))
            self.assertTrue(has_permission(entity, 'realm.identity.act', self.realm))
        self.realm.type = 'server'
        self.realm.save(update_fields=['type'])
        self.assertFalse(has_permission(self.actor, 'realm.identity.act', self.realm, fresh=True))

    def test_fresh_checks_reload_parent_context_and_metadata_after_a_warm_read(self):
        entry = PermissionCatalogEntry.objects.get(code='realm.media.update')
        self.realm.type = 'group'
        self.realm.save(update_fields=['type'])
        self.assertTrue(has_permission(self.actor, entry.code, self.realm))
        parent = Realm.objects.create(type='server', name='Server', created_by=self.actor,
                                      entity=Entity.objects.create(type='realm'))
        Realm.objects.filter(pk=self.realm.pk).update(parent=parent)
        self.assertFalse(has_permission(self.actor, entry.code, self.realm, fresh=True))
        Realm.objects.filter(pk=self.realm.pk).update(parent=None)
        entry.realm_types.set(Type.objects.filter(code='page'))
        self.assertFalse(has_permission(self.actor, entry.code, self.realm, fresh=True))

    def test_existing_nonpage_acting_sessions_use_the_existing_recovery_guard(self):
        from user.backends import AutheticationBackend
        from django.core.exceptions import PermissionDenied
        self.realm.type = 'server'
        self.realm.save(update_fields=['type'])
        account = SimpleNamespace(entity_id=self.actor.pk)
        request = SimpleNamespace(resolver_match=SimpleNamespace(view_name='api-user:user-management'))
        with self.assertRaisesRegex(PermissionDenied, 'REALM_ACCESS_REVOKED'):
            AutheticationBackend()._check_acting_authority(request, account, self.realm.entity)
        request.resolver_match.view_name = 'api-user:entity-switch-back'
        AutheticationBackend()._check_acting_authority(request, account, self.realm.entity)

    def test_catalog_and_role_payloads_are_filtered_and_hidden_grants_survive_save(self):
        hidden = PermissionCatalogEntry.objects.get(code='realm.post.submit')
        disabled = create_permission(code='custom.retired', scope='realm', is_active=False,
                                                        realm_types=['page'])
        self.roles['member'].permissions.add(hidden, disabled)
        response = self.request()
        self.assertEqual(response.status_code, 200)
        data = response.data['result']
        self.assertNotIn(hidden.code, [entry['code'] for entry in data['catalog']])
        member = next(r for r in data['roles'] if r['id'] == self.roles['member'].pk)
        self.assertNotIn(hidden.code, member['permissions'])
        response = self.request('put', {'roles': [{'role_id': self.roles['member'].pk, 'permissions': []}]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(self.roles['member'].permissions.values_list('code', flat=True)), {hidden.code, disabled.code})

    def test_inapplicable_submission_rejects_entire_save(self):
        before = set(self.roles['member'].permissions.values_list('pk', flat=True))
        response = self.request('put', {'roles': [
            {'role_id': self.roles['member'].pk, 'permissions': []},
            {'role_id': self.roles['admin'].pk, 'permissions': ['realm.post.submit']}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(self.roles['member'].permissions.values_list('pk', flat=True)), before)

    def test_switched_delegate_does_not_gain_owner_configuration(self):
        delegate = Entity.objects.create(type='user')
        role = Role.objects.create(realm=self.realm, name='Page publisher')
        role.permissions.add(PermissionCatalogEntry.objects.get(code='realm.identity.act'))
        Member.objects.create(realm=self.realm, entity=delegate, added_by=self.actor, role=role)
        self.assertEqual(self.request(actor=self.realm.entity, personal=delegate).status_code, 403)
        self.assertEqual(self.request(actor=self.realm.entity, personal=self.actor).status_code, 200)

    def test_page_in_another_realm_uses_its_destination_membership(self):
        destination = Realm.objects.create(type='community', name='Club', created_by=self.actor,
                                          entity=Entity.objects.create(type='realm'))
        owner = destination.owned_roles.get(code='owner')
        Member.objects.create(realm=destination, entity=self.realm.entity, added_by=self.actor, role=owner)
        self.realm = destination
        self.assertEqual(self.request(actor=Member.objects.get(realm=destination, role=owner).entity).status_code, 200)

    def test_management_capabilities_fail_closed_without_the_personal_principal(self):
        from community.role_policy import realm_capabilities
        self.assertFalse(realm_capabilities(self.realm.entity, self.realm,
                                            management_actor=None)['can_manage_permissions'])

    def test_catalog_filters_every_managed_kind_including_legacy_channels(self):
        for kind, parent in [('community', None), ('page', None), ('server', None), ('group', None),
                             ('conference', None), ('channel', None), ('voice', None)]:
            self.realm.type = kind
            self.realm.save(update_fields=['type'])
            response = self.request()
            self.assertEqual(response.status_code, 200)
            codes = {row['code'] for row in response.data['result']['catalog']}
            self.assertEqual('realm.identity.act' in codes, kind == 'page')
            self.assertEqual('realm.media.update' in codes, kind in ['community', 'page', 'server', 'group'])
            self.assertEqual('realm.post.submit' in codes, kind == 'community')
            self.assertEqual('realm.request.approve' in codes, kind in ['community', 'server', 'conference'])
            self.assertIn('realm.permissions.manage', codes)

    def test_switched_permission_editor_only_offers_the_persons_delegatable_grants(self):
        delegate = Entity.objects.create(type='user')
        role = Role.objects.create(realm=self.realm, name='Page role editor')
        role.permissions.add(*PermissionCatalogEntry.objects.filter(code__in=['realm.identity.act', 'realm.permissions.manage']))
        Member.objects.create(realm=self.realm, entity=delegate, added_by=self.actor, role=role)
        response = self.request(actor=self.realm.entity, personal=delegate)
        self.assertEqual(response.status_code, 200)
        media = next(entry for entry in response.data['result']['catalog'] if entry['code'] == 'realm.media.update')
        self.assertFalse(media['grantable'])

    def test_permission_matrix_read_queries_do_not_grow_per_role_or_permission(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        def count():
            with CaptureQueriesContext(connection) as queries:
                response = self.request()
            self.assertEqual(response.status_code, 200)
            return len(queries)
        baseline = count()
        for index in range(16):
            role = Role.objects.create(realm=self.realm, name=f'Team {index}')
            role.permissions.add(PermissionCatalogEntry.objects.get(code='realm.member.view'))
            Member.objects.create(realm=self.realm, role=role, entity=Entity.objects.create(type='user'), added_by=self.actor)
        expanded = count()
        self.assertLessEqual(expanded, baseline + 8, (baseline, expanded))

    def test_custom_manager_read_queries_and_peer_protection_are_batched(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        manager = Role.objects.create(realm=self.realm, name='Configuration manager')
        manager.permissions.add(*PermissionCatalogEntry.objects.filter(code__in=[
            'realm.roles.manage', 'realm.permissions.manage', 'realm.member.role.update', 'realm.member.view']))
        Member.objects.filter(realm=self.realm, entity=self.actor).update(role=manager)
        def query():
            with CaptureQueriesContext(connection) as queries:
                response = self.request()
            self.assertEqual(response.status_code, 200)
            return len(queries), response.data['result']
        baseline, _ = query()
        protected = None
        for index in range(16):
            role = Role.objects.create(realm=self.realm, name=f'Contributors {index}')
            role.permissions.add(PermissionCatalogEntry.objects.get(code='realm.member.view'))
            member = Member.objects.create(realm=self.realm, role=role, entity=Entity.objects.create(type='user'), added_by=self.actor)
            if index == 0:
                protected = role.pk
                EntityPermission.objects.create(realm=self.realm, entity=member.entity, effect='grant',
                    permission=PermissionCatalogEntry.objects.get(code='realm.roles.manage'))
        expanded, data = query()
        self.assertLessEqual(expanded, baseline + 8, (baseline, expanded))
        row = next(row for row in data['roles'] if row['id'] == protected)
        self.assertFalse(row['editable'])
        self.assertFalse(row['permissions_editable'])

    def test_manage_invite_stores_personal_grantor_and_rechecks_revocation(self):
        from community.views import InviteView
        from community.invite_review import validate_inviter
        from rest_framework.exceptions import PermissionDenied
        from community.models import Invite
        target = Entity.objects.create(type='user')
        from user.models import Account
        Account.objects.create(entity=target, username='target-manager', email='target@example.invalid', is_active=True)
        request = APIRequestFactory().post('/invites', {'realm_id': self.realm.pk, 'purpose': 'manage',
            'role_id': self.roles['member'].pk, 'target_entity_id': target.pk}, format='json')
        request.entity = self.realm.entity
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, entity=self.actor, entity_id=self.actor.pk))
        with patch('community.invites.is_blocked', return_value=False):
            response = InviteView.as_view()(request)
        self.assertEqual(response.status_code, 201, response.data)
        invite = Invite.objects.get(pk=response.data['result']['id'])
        self.assertEqual(str(invite.created_by_id), str(self.actor.pk))
        validate_inviter(invite)
        Member.objects.filter(realm=self.realm, entity=self.actor).update(role=self.roles['member'])
        from community.invite_rules import InviteError
        with self.assertRaises(InviteError) as error:
            validate_inviter(invite)
        self.assertIn('not allowed', str(error.exception))

    def test_legacy_page_grantor_invites_require_reissue_without_changing_history(self):
        from community.invite_review import validate_inviter
        from community.models import Invite
        from rest_framework.exceptions import PermissionDenied
        invite = Invite.objects.create(realm=self.realm, purpose='manage', created_by=self.realm.entity,
                                       role=self.roles['member'], status='pending')
        with self.assertRaisesRegex(PermissionDenied, 'Revoke and reissue'):
            validate_inviter(invite)
        invite.refresh_from_db()
        self.assertEqual(invite.status, 'pending')
