from django.test import TestCase
from django.utils.timezone import now
from unittest.mock import patch
from rest_framework.test import APIRequestFactory, force_authenticate

from community.tests.test_lifecycle_api import LifecycleFixture
from newsfeed.models import Post, PostTag
from newsfeed.services.post_visibility import can_view_post, visible_posts_filter


class LifecycleVisibilityTests(LifecycleFixture, TestCase):
    def test_review_delivery_does_not_resume_obsolete_activity_after_page_reactivation(self):
        from community.models import Realm, Approvals
        from community.review_delivery import publish_review_effects
        page = self.make('page')
        post = self.post(author=page.entity, destination=self.realm)
        decision = Approvals.objects.create(realm=self.realm, actor=self.actor, target_kind='post', target_id=post.pk,
            action='approve', content_version=post.date_posted, new_state={'approval': 1},
            delivery_state={'status': 'pending', 'first_publication': False})
        Realm.objects.filter(pk=page.pk).update(is_active=False, deactivated_at=now())
        with patch('community.review_delivery.RabbitMQClient.publish', return_value=True) as publish, patch('community.review_delivery.deliver_review_notices') as notices:
            self.assertFalse(publish_review_effects(decision.pk))
            publish.assert_not_called()
            notices.assert_not_called()
            Realm.objects.filter(pk=page.pk).update(is_active=True, deactivated_at=None)
            self.assertFalse(publish_review_effects(decision.pk))
            publish.assert_not_called()
    def test_queued_admin_activity_cannot_use_a_stale_active_realm_object(self):
        from community.models import Realm
        from community.admin_notifications import record_admin_activity
        Realm.objects.filter(pk=self.realm.pk).update(is_active=False, deactivated_at=now())
        notice = {'referenceID': 'event', 'toUserID': str(self.realm.entity_id), 'type': 'community_post_created'}
        with patch('community.admin_notifications._managers', return_value=[]), patch('community.admin_notifications._upsert_notice') as save:
            record_admin_activity(self.realm, notice)
        save.assert_not_called()

    def test_queued_community_activity_does_not_publish_an_inactive_page_post(self):
        from community.notifications import notify_community_activity
        from community.models import Realm
        from entity.models import Entity
        from newsfeed.models import Reaction
        page = self.make('page')
        post = self.post(author=page.entity, destination=self.realm)
        actor = Entity.objects.create(type='user')
        reaction = Reaction.objects.create(post=post, entity=actor)
        with patch('user.services.mongohelpers.NotificationService') as service, patch('user_service.services.rabbitmq.RabbitMQClient.publish'), patch('user_service.services.redis.RedisPubSubClient.publish_json'):
            with self.captureOnCommitCallbacks(execute=True):
                notify_community_activity(post, actor, reaction.pk, [self.actor.pk], notification_type='post_reaction')
            service.return_value.upsert_community_reaction.assert_called_once()
            service.reset_mock()
            Realm.objects.filter(pk=page.pk).update(is_active=False, deactivated_at=now())
            with self.captureOnCommitCallbacks(execute=True):
                notify_community_activity(post, actor, reaction.pk, [self.actor.pk], notification_type='post_reaction')
            service.assert_not_called()

    def test_surviving_bare_realm_identity_has_no_profile_or_link(self):
        from entity.models import Entity
        from entity.serializers import EntitySerializer
        from entity.utils import get_entity_name, get_entity_display_username, get_entity_profile_path
        entity = Entity.objects.create(type='realm')
        details = EntitySerializer(entity).data['details']
        self.assertIsNotNone(details)
        self.assertEqual(details['first_name'], 'Deleted realm')
        self.assertEqual(details['profile'], 'none')
        self.assertFalse(details['is_active'])
        self.assertIsNone(details['slug'])
        self.assertEqual(get_entity_name(entity), 'Deleted realm')
        self.assertEqual(get_entity_display_username(entity), 'Deleted realm')
        self.assertIsNone(get_entity_profile_path(entity))

    def interact(self, post, actor=None, fail=False):
        from rest_framework.views import APIView
        from rest_framework.response import Response
        from community.post_access import CommunityPostAccessMixin
        from entity.models import Entity

        class Interaction(CommunityPostAccessMixin, APIView):
            def post(self, request):
                Entity.objects.create(id='guard-write', type='bot')
                return Response({'written': True}, status=400 if fail else 200)

        request = APIRequestFactory().post('/interaction', {'post_id': post.pk}, format='json')
        force_authenticate(request, user=self.account)
        request.entity = actor or self.actor
        return Interaction.as_view()(request)

    def test_inactive_page_cannot_interact_with_an_active_personal_post(self):
        from entity.models import Entity
        page = self.make('page', is_active=False, deactivated_at=now())
        response = self.interact(self.post(), actor=page.entity)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(Entity.objects.filter(pk='guard-write').exists())

    def test_rejected_interaction_rolls_back_its_writes(self):
        from entity.models import Entity
        response = self.interact(self.post(), actor=self.make('page').entity, fail=True)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Entity.objects.filter(pk='guard-write').exists())

    def test_active_page_interaction_keeps_existing_behavior(self):
        page = self.make('page')
        self.assertEqual(self.interact(self.post(), actor=page.entity).status_code, 200)

    def test_join_request_cannot_target_an_active_child_of_an_inactive_server(self):
        from community.views import InviteView
        from community.models import Invite
        from rest_framework.exceptions import PermissionDenied
        server = self.make('server', is_active=False, deactivated_at=now())
        child = self.make('channel', parent=server)
        with self.assertRaises(PermissionDenied):
            InviteView()._request_access(child, self.account, self.actor)
        self.assertFalse(Invite.objects.filter(realm=child).exists())

    def test_inactive_page_cannot_send_an_invitation_even_when_owning_target(self):
        from community.invites import create_invite
        from community.models import Member
        from entity.tests.permission_fixtures import create_permission
        from rest_framework.exceptions import PermissionDenied
        for code in ('realm.invite.create', 'realm.invite.publish'):
            create_permission(code=code, scope='realm', realm_types=['community'])
        page = self.make('page', is_active=False, deactivated_at=now())
        target = self.make('page')
        Member.objects.filter(realm=self.realm, entity=self.actor).update(entity=page.entity)
        with self.assertRaises(PermissionDenied):
            create_invite(self.realm, page.entity, target_entity_id=target.entity_id)

    def test_inactive_page_cannot_join_an_active_public_community(self):
        from community.community_views import CommunityJoinView
        from community.models import Member
        page = self.make('page', is_active=False, deactivated_at=now())
        request = APIRequestFactory().post('/join', {}, format='json')
        force_authenticate(request, user=self.account)
        request.entity = page.entity
        response = CommunityJoinView.as_view()(request, realm_id=self.realm.pk)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(Member.objects.filter(realm=self.realm, entity=page.entity).exists())

    def post(self, author=None, destination=None, **kwargs):
        post = Post.objects.create(entity=author or self.actor, file_type='text',
            content_type='text', caption='Caption', **kwargs)
        if destination:
            PostTag.objects.create(post=post, entity=destination.entity)
        return post

    def test_deleting_community_is_hidden_even_with_stale_active_flag(self):
        post = self.post(destination=self.realm)
        self.realm.deletion_requested_at = now()
        self.realm.save(update_fields=['deletion_requested_at'])
        self.assertFalse(can_view_post(post, self.actor))
        self.assertFalse(Post.objects.filter(visible_posts_filter(self.actor), pk=post.pk).exists())

    def test_inactive_page_author_is_hidden_from_public_and_own_reads(self):
        page = self.make('page')
        post = self.post(author=page.entity)
        page.is_active = False
        page.save(update_fields=['is_active'])
        for viewer in (None, self.actor, page.entity):
            self.assertFalse(can_view_post(post, viewer))
            self.assertFalse(Post.objects.filter(visible_posts_filter(viewer), pk=post.pk).exists())

    def test_unrelated_ordinary_posts_survive_realm_deactivation(self):
        ordinary = self.post()
        scoped = self.post(destination=self.realm)
        self.realm.is_active = False
        self.realm.save(update_fields=['is_active'])
        actual = set(Post.objects.filter(visible_posts_filter(self.actor)).values_list('pk', flat=True))
        self.assertEqual(actual, {ordinary.pk})
        self.assertNotIn(scoped.pk, actual)

    def test_submissions_cannot_open_an_inactive_community(self):
        from newsfeed.submission_views import author_submissions
        post = self.post(destination=self.realm, approval=0)
        self.assertTrue(author_submissions(self.actor).filter(pk=post.pk).exists())
        self.realm.is_active = False
        self.realm.save(update_fields=['is_active'])
        self.assertFalse(author_submissions(self.actor).filter(pk=post.pk).exists())

    def test_my_realms_list_does_not_keep_showing_inactive_owned_realms(self):
        from community.views import MyRealms
        self.realm.is_active = False
        self.realm.save(update_fields=['is_active'])
        request = APIRequestFactory().get('/my-list', {'type': 'community'})
        force_authenticate(request, user=self.account)
        request.entity = self.actor
        response = MyRealms.as_view()(request)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['results'], [])

    def test_generic_page_profile_is_unavailable_to_its_owner_when_disabled(self):
        from user.views import UserAuthentication
        page = self.make('page', slug='sleeping-page', is_active=False, deactivated_at=now())
        request = APIRequestFactory().get('/sleeping-page')
        force_authenticate(request, user=self.account)
        request.entity = self.actor
        with patch('user.views.save_profile_visit'), patch('user.views.RabbitMQClient.publish_on_commit'):
            response = UserAuthentication.as_view()(request, username=page.slug)
        self.assertEqual(response.status_code, 404, response.data)

    def test_disabled_page_cannot_receive_new_follow_activity_by_entity_id(self):
        from community.views import FollowRealmView
        from entity.models import Follow
        page = self.make('page', is_active=False, deactivated_at=now())
        request = APIRequestFactory().post('/follow', {'entity_id': page.entity_id}, format='json')
        force_authenticate(request, user=self.account)
        request.entity = self.actor
        with patch.object(FollowRealmView, '_notify_new_follower'), patch('community.views.publish_profile_relationship_update'):
            response = FollowRealmView.as_view()(request)
        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(Follow.objects.filter(followee=page.entity).exists())

    def test_inactive_page_cannot_create_a_new_owned_community(self):
        from community.community_views import CommunitiesView
        from community.models import Realm
        from entity.tests.permission_fixtures import create_permission
        create_permission(code='realm.server.create', scope='global')
        page = self.make('page', is_active=False, deactivated_at=now())
        request = APIRequestFactory().post('/communities', {'name': 'New community',
            'slug': 'new-space', 'is_private': False}, format='json')
        force_authenticate(request, user=self.account)
        request.entity = page.entity
        response = CommunitiesView.as_view()(request)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(Realm.objects.filter(created_by=page.entity).exists())
