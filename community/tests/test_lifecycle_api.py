from importlib import import_module, util
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.utils.timezone import now
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Member, Realm
from entity.models import Entity, PermissionCatalogEntry
from entity.roles import get_default_role
from entity.tests.permission_fixtures import create_permission


class LifecycleFixture:
    def setUp(self):
        self.actor = Entity.objects.create(type='user')
        self.account = SimpleNamespace(entity=self.actor, entity_id=self.actor.pk,
                                       is_active=True, is_authenticated=True)
        seed = import_module('entity.migrations.0030_realm_lifecycle_permissions')
        seed.seed_lifecycle_permissions(apps, SimpleNamespace(connection=connection))
        create_permission(code='realm.identity.act', scope='realm', realm_types=['page'])
        self.realm = self.make('community')

    def make(self, kind, owner=None, **kwargs):
        realm = Realm.objects.create(entity=Entity.objects.create(type='realm'),
            name=f'My {kind}', type=kind, created_by=self.actor, **kwargs)
        Member.objects.create(entity=owner or self.actor, realm=realm,
            added_by=self.actor, role=get_default_role('owner', realm=realm))
        return realm

    def call(self, action, realm=None, data=None, actor=None):
        self.assertIsNotNone(util.find_spec('community.lifecycle_views'), 'Lifecycle endpoints are missing')
        views = import_module('community.lifecycle_views')
        request = APIRequestFactory().post('/lifecycle', data or {}, format='json')
        force_authenticate(request, user=self.account)
        request.entity = actor or self.actor
        return getattr(views, action).as_view()(request, realm_id=(realm or self.realm).pk)


class LifecycleApiTests(LifecycleFixture, TestCase):
    def test_channel_management_returns_only_accessible_parent_destination(self):
        server=self.make('server',is_private=True)
        channel=self.make('channel',parent=server)
        from community.lifecycle_views import RealmLifecycleView
        def inspect():
            request=APIRequestFactory().get('/lifecycle');force_authenticate(request,user=self.account);request.entity=self.actor
            return RealmLifecycleView.as_view()(request,realm_id=channel.pk).data['result']
        self.assertEqual(inspect().get('return_server_id'),str(server.pk))
        Member.objects.filter(realm=server,entity=self.actor).delete()
        self.assertIsNone(inspect().get('return_server_id'))
    def test_delete_rejects_unsupported_descendant_kinds_before_intent(self):
        server=self.make('server')
        self.make('page',parent=server)
        response=self.call('RealmDeleteView',server,{'confirm_name':server.name})
        self.assertEqual(response.status_code,409)
        server.refresh_from_db()
        self.assertIsNone(server.deletion_requested_at)
    def test_lifecycle_only_delegate_has_management_entry(self):
        from community.role_policy import realm_capabilities
        membership = Member.objects.get(entity=self.actor, realm=self.realm)
        membership.role = get_default_role('member', realm=self.realm)
        membership.save()
        membership.role.permissions.add(PermissionCatalogEntry.objects.get(code='realm.deactivate'))
        caps = realm_capabilities(self.actor, self.realm)
        self.assertTrue(caps.get('can_deactivate_realm'))
        self.assertFalse(caps.get('can_delete_realm'))
        self.assertTrue(caps['can_manage'])

    def test_all_kinds_preserve_data_and_child_flags_on_deactivation(self):
        for kind in ('community', 'page', 'server', 'group', 'conference', 'channel', 'voice'):
            realm = self.make(kind)
            child = self.make('channel', parent=realm) if kind == 'server' else None
            response = self.call('RealmDeactivateView', realm)
            self.assertEqual(response.status_code, 200, response.data)
            realm.refresh_from_db()
            self.assertFalse(realm.is_active)
            self.assertIsNotNone(realm.deactivated_at)
            self.assertTrue(Member.objects.filter(realm=realm).exists())
            if child:
                child.refresh_from_db()
                self.assertTrue(child.is_active)

    def test_delete_requires_current_exact_name_then_is_irreversible_intent(self):
        self.assertEqual(self.call('RealmDeleteView', data={'confirm_name': 'wrong'}).status_code, 409)
        self.realm.refresh_from_db()
        self.assertIsNone(self.realm.deletion_requested_at)
        result = self.call('RealmDeleteView', data={'confirm_name': self.realm.name})
        self.assertEqual(result.status_code, 202, result.data)
        self.realm.refresh_from_db()
        self.assertFalse(self.realm.is_active)
        marker = self.realm.deletion_requested_at
        self.assertIsNotNone(marker)
        self.assertEqual(self.call('RealmDeleteView', data={'confirm_name': self.realm.name}).status_code, 202)
        self.realm.refresh_from_db()
        self.assertEqual(self.realm.deletion_requested_at, marker)
        self.assertEqual(self.call('RealmReactivateView', data={'actor_entity_id': self.actor.pk}).status_code, 403)

    def test_delegated_deactivation_does_not_grant_delete(self):
        member = Member.objects.get(entity=self.actor, realm=self.realm)
        member.role = get_default_role('member', realm=self.realm)
        member.save()
        self.assertEqual(self.call('RealmDeactivateView').status_code, 403)
        member.role.permissions.add(PermissionCatalogEntry.objects.get(code='realm.deactivate'))
        self.assertEqual(self.call('RealmDeleteView', data={'confirm_name': self.realm.name}).status_code, 403)
        self.assertEqual(self.call('RealmDeactivateView').status_code, 200)

    def test_parent_owner_can_deactivate_private_channel_without_membership(self):
        server = self.make('server')
        child = self.make('channel', owner=Entity.objects.create(type='user'), parent=server, is_private=True)
        self.assertEqual(self.call('RealmDeactivateView', child).status_code, 200)

    def test_page_deletion_blocks_owned_independent_realms(self):
        page = self.make('page')
        self.make('community', owner=page.entity)
        response = self.call('RealmDeleteView', page, {'confirm_name': page.name})
        self.assertEqual(response.status_code, 409, response.data)
        page.refresh_from_db()
        self.assertIsNone(page.deletion_requested_at)

    def test_child_cannot_be_reactivated_through_inactive_parent(self):
        child = self.make('channel', parent=self.realm)
        self.call('RealmDeactivateView', child)
        self.call('RealmDeactivateView')
        response = self.call('RealmReactivateView', child, {'actor_entity_id': self.actor.pk})
        self.assertEqual(response.status_code, 409, response.data)

    def test_legacy_restriction_and_expiry_cannot_be_reactivated(self):
        self.realm.is_active = False
        self.realm.save(update_fields=['is_active'])
        self.assertEqual(self.call('RealmReactivateView', data={'actor_entity_id': self.actor.pk}).status_code, 403)
        conference = self.make('conference', expires_at=now(), is_active=False, deactivated_at=now())
        self.assertEqual(self.call('RealmReactivateView', conference, {'actor_entity_id': self.actor.pk}).status_code, 403)
