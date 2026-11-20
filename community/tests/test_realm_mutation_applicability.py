from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Follow, Invite, Member, Realm
from community.tests import test_permission_applicability_api as fixtures
from entity.models import Entity, PermissionCatalogEntry


class RealmMutationApplicabilityTests(TestCase):
    setUp = fixtures.PermissionApplicabilityApiTests.setUp

    def call(self, view, method, data, *, actor=None, personal=None):
        request = getattr(APIRequestFactory(), method)('/realms', data=data, format='json')
        request.entity = actor or self.actor
        person = personal or self.actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, entity=person, entity_id=person.pk))
        return view.as_view()(request)

    def test_details_cannot_reclassify_realms_or_bypass_the_media_route(self):
        from community.views import MyRealms
        for fields in ({'type': 'server'}, {'parent_id': self.realm.pk}, {'entity_id': str(self.actor.pk)},
                       {'profile': 'https://example.invalid/photo.jpg'}, {'cover_photo': 'photo.jpg'}):
            response = self.call(MyRealms, 'put', {'realm_id': self.realm.pk, 'fields': fields})
            self.assertEqual(response.status_code, 400, fields)
        self.realm.refresh_from_db()
        self.assertEqual(self.realm.type, 'page')

    def test_details_permission_removal_is_checked_despite_a_stale_catalog_cache(self):
        from community.views import MyRealms
        from entity.services.permission_catalog_cache import get_catalog
        get_catalog()
        with patch('entity.services.permission_catalog_cache._redis_delete'):
            PermissionCatalogEntry.objects.get(code='realm.update').realm_types.clear()
            response = self.call(MyRealms, 'put', {'realm_id': self.realm.pk, 'fields': {'name': 'Forbidden change'}})
        self.assertIn(response.status_code, (401, 403))
        self.realm.refresh_from_db()
        self.assertEqual(self.realm.name, 'Page')

    def test_duplicate_follower_handler_cannot_delete_another_realms_follower(self):
        from community.views import InviteView
        other = Realm.objects.create(type='page', name='Other', created_by=self.actor,
            entity=Entity.objects.create(type='realm'))
        follow = Follow.objects.create(followee=other.entity, follower=Entity.objects.create(type='user'), status=True)
        response = self.call(InviteView, 'delete', {'realm_id': self.realm.pk, 'follow_id': str(follow.pk)})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Follow.objects.filter(pk=follow.pk).exists())

    def test_own_page_manage_invite_revoke_requires_the_persons_authority(self):
        from community.invite_review import transition_invite
        from rest_framework.exceptions import PermissionDenied
        delegate = Entity.objects.create(type='user')
        role = self.realm.owned_roles.create(name='Identity delegate')
        role.permissions.add(PermissionCatalogEntry.objects.get(code='realm.identity.act'))
        Member.objects.create(realm=self.realm, entity=delegate, role=role, added_by=self.actor)
        for sender in (self.actor, self.realm.entity):
            invite = Invite.objects.create(realm=self.realm, purpose='manage', role=self.roles['member'],
                                           created_by=sender, status='pending')
            with self.assertRaises(PermissionDenied):
                transition_invite(actor=self.realm.entity, account=SimpleNamespace(entity=delegate),
                                  invite_id=invite.pk, action='revoked')
            invite.refresh_from_db()
            self.assertEqual(invite.status, 'pending')

    def test_identity_relation_is_checked_when_the_lazy_query_executes(self):
        from entity.services.acting import acting_memberships
        query = acting_memberships(self.actor)
        PermissionCatalogEntry.objects.get(code='realm.identity.act').realm_types.clear()
        self.assertFalse(query.filter(realm=self.realm).exists())

    def test_notice_relation_is_checked_when_the_lazy_owner_audience_executes(self):
        from community.notification_permissions import permitted_members
        query = permitted_members(self.realm, ['realm.member.view'])
        PermissionCatalogEntry.objects.get(code='realm.member.view').realm_types.clear()
        self.assertFalse(query.exists())

    def test_page_directory_payloads_include_custom_manager_entry_hints(self):
        from community.views import MyRealms, TopRealms, FollowRealmView
        role = self.realm.owned_roles.create(name='Role manager')
        role.permissions.add(PermissionCatalogEntry.objects.get(code='realm.roles.manage'))
        Member.objects.filter(realm=self.realm, entity=self.actor).update(role=role)
        Follow.objects.create(followee=self.realm.entity, follower=self.actor, status=True)
        for view in (MyRealms, TopRealms, FollowRealmView):
            response = self.call(view, 'get', {'type': 'page'})
            self.assertEqual(response.status_code, 200, response.data)
            entry = next(row for row in response.data['results'] if row['id'] == self.realm.pk)
            self.assertTrue(entry.get('capabilities', {}).get('can_manage_roles'), view.__name__)
            self.assertFalse(entry['is_admin'])

    def test_request_reviewer_can_only_read_the_filtered_pending_request_queue(self):
        from community.views import InviteView
        self.realm.type = 'server'
        self.realm.save(update_fields=['type'])
        role = self.realm.owned_roles.create(name='Request reviewer')
        role.permissions.add(PermissionCatalogEntry.objects.get(code='realm.request.approve'))
        Member.objects.filter(realm=self.realm, entity=self.actor).update(role=role)
        requester = Entity.objects.create(type='user')
        from user.models import Account
        Account.objects.create(entity=requester, username='requester', email='requester@example.invalid')
        pending = Invite.objects.create(realm=self.realm, kind='request', status='pending', created_by=requester,
                                        target_entity=requester)
        Invite.objects.create(realm=self.realm, kind='request', status='accepted', created_by=requester, target_entity=requester)
        Invite.objects.create(realm=self.realm, kind='invite', status='pending', created_by=self.actor, target_entity=requester)
        response = self.call(InviteView, 'get', {'realm_id': self.realm.pk, 'kind': 'request', 'status': 'pending'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([str(row['id']) for row in response.data['result']], [str(pending.pk)])
        self.assertIn(self.call(InviteView, 'get', {'realm_id': self.realm.pk}).status_code, (401, 403))
