from importlib import import_module, util

from django.utils.timezone import now
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Member, Realm
from entity.models import Entity, EntityPermission, PermissionCatalogEntry
from entity.roles import get_default_role
from community.tests.test_lifecycle_api import LifecycleFixture


class LifecycleRecoveryTests(LifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        # A page, rather than the person, is a member of this inactive target.
        self.page = self.make('page')
        self.target = self.make('community', owner=Entity.objects.create(type='user'),
                                is_active=False, deactivated_at=now())
        self.page_member = Member.objects.create(entity=self.page.entity, realm=self.target,
            added_by=self.actor, role=get_default_role('member', realm=self.target))
        self.permission = PermissionCatalogEntry.objects.get(code='realm.reactivate')

    def listing(self):
        self.assertIsNotNone(util.find_spec('community.lifecycle_views'), 'Recovery endpoint is missing')
        views = import_module('community.lifecycle_views')
        request = APIRequestFactory().get('/realms/deactivated')
        force_authenticate(request, user=self.account)
        request.entity = self.actor
        return views.DeactivatedRealmsView.as_view()(request)

    def test_both_page_hops_required(self):
        self.assertEqual(self.call('RealmReactivateView', self.target,
            {'actor_entity_id': self.page.entity_id}).status_code, 403)
        self.page_member.role.permissions.add(self.permission)
        response = self.call('RealmReactivateView', self.target, {'actor_entity_id': self.page.entity_id})
        self.assertEqual(response.status_code, 200, response.data)

    def test_either_revocation_blocks_confirmation(self):
        self.page_member.role.permissions.add(self.permission)
        listed = self.listing()
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(len(listed.data['result']['items']), 1)
        # Downgrade the personal page role: target grant alone must not work.
        personal = Member.objects.get(entity=self.actor, realm=self.page)
        personal.role = get_default_role('member', realm=self.page)
        personal.save()
        response = self.call('RealmReactivateView', self.target, {'actor_entity_id': self.page.entity_id})
        self.assertEqual(response.status_code, 403)

    def test_inactive_source_page_is_not_recovery_authority(self):
        self.page_member.role.permissions.add(self.permission)
        self.page.is_active = False
        self.page.save(update_fields=['is_active'])
        self.assertEqual(self.call('RealmReactivateView', self.target,
            {'actor_entity_id': self.page.entity_id}).status_code, 403)

    def test_no_recursive_page_recovery_or_forged_identity(self):
        foreign = Entity.objects.create(type='user')
        self.assertEqual(self.call('RealmReactivateView', self.target,
            {'actor_entity_id': foreign.pk}).status_code, 403)

    def test_duplicate_actor_paths_are_deduped_and_no_settings_are_exposed(self):
        self.page_member.role.permissions.add(self.permission)
        Member.objects.create(entity=self.actor, realm=self.target, added_by=self.actor,
                              role=get_default_role('owner', realm=self.target))
        response = self.listing()
        items = response.data['result']['items']
        self.assertEqual(len(items), 1)
        self.assertEqual({row['entity_id'] for row in items[0]['actors']},
                         {str(self.actor.pk), str(self.page.entity_id)})
        self.assertNotIn('description', items[0])
        self.assertNotIn('members', items[0])

    def test_deny_override_revokes_page_target_permission(self):
        self.page_member.role.permissions.add(self.permission)
        EntityPermission.objects.create(entity=self.page.entity, realm=self.target,
                                        permission=self.permission, effect='deny')
        self.assertEqual(self.call('RealmReactivateView', self.target,
            {'actor_entity_id': self.page.entity_id}).status_code, 403)

    def test_recovery_query_budget_does_not_grow_with_list_length(self):
        for _ in range(20):
            self.make('community', is_active=False, deactivated_at=now())
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as queries:
            response = self.listing()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['result']['items']), 20)
        self.assertLessEqual(len(queries), 8)

    def test_private_legacy_channel_recovery_uses_parent_owner_and_channel_kind(self):
        server = self.make('server')
        child = self.make('group', owner=Entity.objects.create(type='user'), parent=server,
                          is_private=True, is_active=False, deactivated_at=now())
        items = self.listing().data['result']['items']
        item = next(row for row in items if row['realm_id'] == child.pk)
        self.assertEqual(item['kind'], 'channel')
        response = self.call('RealmReactivateView', child, {'actor_entity_id': self.actor.pk})
        self.assertEqual(response.status_code, 200, response.data)

    def test_parent_delegate_has_no_individual_child_authority(self):
        server = self.make('server')
        member = Member.objects.get(entity=self.actor, realm=server)
        member.role = get_default_role('member', realm=server)
        member.save()
        member.role.permissions.add(self.permission)
        child = self.make('channel', owner=Entity.objects.create(type='user'), parent=server,
                          is_private=True, is_active=False, deactivated_at=now())
        self.assertNotIn(child.pk, [row['realm_id'] for row in self.listing().data['result']['items']])
        self.assertEqual(self.call('RealmReactivateView', child, {'actor_entity_id': self.actor.pk}).status_code, 403)
