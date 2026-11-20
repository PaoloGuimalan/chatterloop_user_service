from entity.tests.permission_fixtures import create_permission
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from django.utils.timezone import now
from rest_framework.test import APIRequestFactory, force_authenticate
from community.models import Member
from entity.models import Entity, PermissionCatalogEntry, Role
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture
from newsfeed.models import Post, PostTag


class CommunityArchiveTests(RealmRolePolicyFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch('newsfeed.serializers.get_preview', return_value=None))
        self.archive = create_permission(code='realm.post.archive', scope='realm')
        self.delegate = Entity.objects.create(type='user')
        self.role = Role.objects.create(realm=self.realm, name='Archivist')
        self.role.permissions.add(self.archive)
        self.member = Member.objects.create(realm=self.realm, entity=self.delegate, role=self.role, added_by=self.actor)
        self.post = self.make_post(self.realm.entity)

    def make_post(self, entity, **fields):
        post = Post.objects.create(entity=entity, file_type='text', content_type='text', **fields)
        PostTag.objects.create(post=post, entity=self.realm.entity)
        return post

    def request(self, actor, archived=None, post=None, extra=None):
        views = import_module('community.archive_views')
        request = (APIRequestFactory().get('/archive') if archived is None else
            APIRequestFactory().post('/archive/post', {'is_archived': archived, **(extra or {})}, format='json'))
        request.entity = actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        return (views.CommunityArchiveView.as_view()(request, realm_id=self.realm.pk) if archived is None else
            views.CommunityArchivePostView.as_view()(request, realm_id=self.realm.pk, post_id=(post or self.post).pk))

    def test_owner_and_current_archive_delegate_can_archive_only_community_authored_posts(self):
        original = self.post.updated_at
        self.assertEqual(self.request(self.delegate, True).status_code, 200)
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_archived)
        self.assertEqual(self.post.updated_at, original)
        self.assertEqual(self.request(self.actor, False).status_code, 200)
        personal = self.make_post(self.actor)
        self.assertEqual(self.request(self.actor, True, post=personal).status_code, 404)
        self.assertEqual(self.request(self.delegate, True, post=personal).status_code, 404)
        self.role.permissions.clear()
        self.assertEqual(self.request(self.delegate, True).status_code, 403)

    def test_only_the_communitys_archive_is_returned_and_owner_privileges_are_not_forged(self):
        self.request(self.delegate, True)
        self.make_post(self.delegate, is_archived=True)
        self.make_post(self.actor, is_archived=True)
        self.make_post(self.realm.entity, is_archived=True, deleted_at=now())
        response = self.request(self.delegate)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['post_id'] for item in response.data['results']], [self.post.pk])
        card = response.data['results'][0]
        self.assertTrue(card['can_archive'])
        self.assertFalse(card['is_owner'])
        self.assertTrue(card['read_only'])

    def test_restore_never_approves_or_restores_platform_removal(self):
        Post.objects.filter(pk=self.post.pk).update(approval=2, is_archived=True)
        self.assertEqual(self.request(self.delegate, False).status_code, 200)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_archived)
        self.assertEqual(self.post.approval, 2)
        Post.objects.filter(pk=self.post.pk).update(deleted_at=now(), is_archived=True)
        self.assertEqual(self.request(self.actor, False).status_code, 404)

    def test_generic_post_editor_stays_owner_only_even_for_archive_delegate(self):
        from newsfeed.services.post_editing import update_post
        from rest_framework.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            update_post(request=SimpleNamespace(entity=self.delegate), post_id=self.post.pk, fields={'is_archived': True})
        self.assertEqual(self.request(self.delegate, 'true').status_code, 400)
        self.assertEqual(self.request(self.delegate, True, extra={'caption': 'Changed'}).status_code, 400)

    def test_removed_members_and_foreign_community_posts_are_denied(self):
        foreign = self.make_post(self.other.entity)
        self.assertEqual(self.request(self.delegate, True, post=foreign).status_code, 404)
        self.member.delete()
        self.assertEqual(self.request(self.delegate).status_code, 403)
