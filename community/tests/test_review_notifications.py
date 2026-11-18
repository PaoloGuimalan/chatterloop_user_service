from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils.timezone import now
from rest_framework.test import APIRequestFactory, force_authenticate

from community import admin_notifications as inbox
from community.models import Approvals, Invite, Member
from community.review_notifications import deliver_review_notices
from community.tests.notification_memory import NotificationMemory
from entity.models import Entity, EntityPermission, PermissionCatalogEntry, Role
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture
from newsfeed.models import Post, PostTag
from user.ext_models.mongomodels import Notification


class ReviewNotificationTests(RealmRolePolicyFixture, TestCase):
    def setUp(self):
        super().setUp()
        for code in ('realm.invite.review', 'realm.notifications.view', 'realm.request.approve', 'realm.request.decline'):
            self.catalog[code] = PermissionCatalogEntry.objects.create(code=code, scope='realm')
        self.memory = NotificationMemory()
        self.enterContext(patch.object(Notification, 'objects', self.memory))
        self.enterContext(patch.object(Notification, 'save', self.memory.save_document))
        self.enterContext(patch.object(inbox.RedisPubSubClient, 'publish_json'))
        self.push = self.enterContext(patch('user_service.services.rabbitmq.RabbitMQClient.publish', return_value=True))
        self.enterContext(patch.object(inbox, '_timestamp', return_value='2026-11-16T00:00:00.000Z'))
        self.author = Entity.objects.create(type='user')
        self.reviewer = Entity.objects.create(type='user')
        self.viewer = Entity.objects.create(type='user')
        role = Role.objects.create(realm=self.realm, name='Post Reviewer')
        role.permissions.add(self.catalog['realm.post.review'])
        self.reviewer_role = role
        Member.objects.create(realm=self.realm, entity=self.reviewer, added_by=self.actor, role=role)
        role = Role.objects.create(realm=self.realm, name='Inbox reader')
        role.permissions.add(self.catalog['realm.notifications.view'])
        Member.objects.create(realm=self.realm, entity=self.viewer, added_by=self.actor, role=role)
        self.post = Post.objects.create(entity=self.author, file_type='text', content_type='text', approval=0,
                                        caption='Hidden caption', is_anonymous=True)
        PostTag.objects.create(post=self.post, entity=self.realm.entity)
        self.decision = Approvals.objects.create(realm=self.realm, actor=self.author, target_kind='post',
            target_id=self.post.pk, action='submit', content_version=self.post.date_posted, new_state={'approval': 0})

    def get(self, actor, **params):
        request = APIRequestFactory().get('/notifications', params)
        request.entity = actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        return inbox.CommunityNotificationsView.as_view()(request, realm_id=self.realm.pk)

    def deliver(self):
        deliver_review_notices(self.decision, self.post, self.realm)

    def test_post_only_reviewer_receives_pending_receipt_and_fixed_preview_without_join_powers(self):
        self.deliver()
        receipts = [n for n in self.memory.rows if n.type == inbox.ADMIN_INBOX and n.toUserID == str(self.reviewer.pk)]
        self.assertEqual(len(receipts), 1)
        previews = [n for n in self.memory.rows if n.type == inbox.ADMIN_ACTIVITY and n.toUserID == str(self.reviewer.pk)]
        self.assertEqual(len(previews), 1)
        self.assertIn('1 new activity', previews[0].content.details)
        self.assertFalse(any(n.type == inbox.POST_CREATED for n in self.memory.rows))
        self.assertFalse(any(n.type == inbox.ADMIN_INBOX and n.toUserID == str(self.viewer.pk) for n in self.memory.rows))
        response = self.get(self.reviewer)
        self.assertEqual(response.status_code, 200)
        row = response.data['results'][0]
        self.assertEqual(row['approval_id'], str(self.decision.pk))
        self.assertEqual(row['approval_kind'], 'posts')
        self.assertTrue(row['is_anonymous'])
        self.assertIsNone(row['actor'])
        self.assertNotIn('Hidden caption', str(response.data))
        self.assertEqual(self.get(self.viewer).data['count'], 0)

    def test_current_permission_revocation_removes_inbox_access_and_delivery(self):
        self.reviewer_role.permissions.clear()
        self.deliver()
        self.assertEqual(self.get(self.reviewer).status_code, 403)
        self.assertFalse(any(n.toUserID == str(self.reviewer.pk) for n in self.memory.rows))

    def test_explicit_deny_and_inactive_capability_exclude_delivery_but_owner_is_intrinsic(self):
        EntityPermission.objects.create(realm=self.realm, entity=self.reviewer,
            permission=self.catalog['realm.post.review'], effect='deny')
        self.deliver()
        self.assertFalse(any(n.toUserID == str(self.reviewer.pk) for n in self.memory.rows))
        self.assertTrue(any(n.toUserID == str(self.actor.pk) and n.type == inbox.ADMIN_ACTIVITY for n in self.memory.rows))
        self.memory.rows.clear()
        self.catalog['realm.post.review'].is_active = False
        self.catalog['realm.post.review'].save(update_fields=['is_active'])
        self.deliver()
        self.assertFalse(any(n.type == inbox.ADMIN_INBOX for n in self.memory.rows))

    def test_retries_preserve_independent_read_state_and_count(self):
        self.deliver()
        row = next(n for n in self.memory.rows if n.type == 'community_post_pending')
        inbox.acknowledge_activity(self.realm, self.reviewer, 0, [row.notificationID])
        self.deliver()
        preview = next(n for n in self.memory.rows if n.type == inbox.ADMIN_ACTIVITY and n.toUserID == str(self.reviewer.pk))
        self.assertTrue(preview.isRead)
        other = next(n for n in self.memory.rows if n.type == inbox.ADMIN_ACTIVITY and n.toUserID == str(self.actor.pk))
        self.assertFalse(other.isRead)
        self.assertIn('1 new activity', other.content.details)

    def test_resolved_pending_notice_is_settled_without_acknowledging_a_later_edit(self):
        self.deliver()
        old = self.decision
        from datetime import timedelta
        self.post.updated_at = old.content_version + timedelta(seconds=1)
        Post.objects.filter(pk=self.post.pk).update(updated_at=self.post.updated_at)
        newer = Approvals.objects.create(realm=self.realm, actor=self.author, target_kind='post',
            target_id=self.post.pk, action='edit', content_version=self.post.updated_at, new_state={'approval': 0})
        deliver_review_notices(newer, self.post, self.realm)
        inbox.settle_review_notices(self.realm, 'post', self.post.pk, through_version=old.content_version)
        pending = [n for n in self.memory.rows if n.type == 'community_post_pending']
        self.assertEqual(len(pending), 2)
        self.assertTrue(next(n for n in pending if n.referenceID == str(old.pk)).referenceStatus)
        self.assertFalse(next(n for n in pending if n.referenceID == str(newer.pk)).referenceStatus)

    def test_invite_reviewer_receives_only_its_pending_section(self):
        self.reviewer_role.permissions.set([self.catalog['realm.invite.review']])
        invite = Invite.objects.create(realm=self.realm, created_by=self.author, target_entity=self.viewer,
                                        kind='invite', status='for_approval')
        decision = Approvals.objects.create(realm=self.realm, actor=self.author, target_kind='invite',
            target_id=str(invite.pk), action='submit')
        from community.invite_review import notify_invite_review_activity
        notify_invite_review_activity(decision.pk)
        self.deliver()
        response = self.get(self.reviewer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['approval_kind'], 'invites')
        self.assertEqual(response.data['results'][0]['approval_id'], str(invite.pk))

    def test_rejection_resolves_pending_receipts_and_addresses_decision_to_true_author(self):
        self.deliver()
        Post.objects.filter(pk=self.post.pk).update(approval=2)
        self.post.approval = 2
        rejected = Approvals.objects.create(realm=self.realm, actor=self.actor, target_kind='post',
            target_id=self.post.pk, action='reject', content_version=self.post.date_posted,
            new_state={'approval': 2}, reason='Outside our topic')
        deliver_review_notices(rejected, self.post, self.realm)
        pending = next(n for n in self.memory.rows if n.type == 'community_post_pending')
        self.assertTrue(pending.referenceStatus)
        self.assertTrue(all(n.isRead for n in self.memory.rows if n.type == inbox.ADMIN_INBOX))
        notice = next(n for n in self.memory.rows if n.type == 'community_post_decision')
        self.assertEqual(notice.toUserID, str(self.author.pk))
        self.assertEqual(notice.fromUserID, str(self.realm.entity_id))
        self.assertEqual(notice.target.type, 'realm_review')
        self.assertEqual(notice.target.supportingID, str(rejected.pk))
        payload = self.push.call_args.args[1]
        self.assertEqual(payload['entity_ids'], [str(self.author.pk)])
        self.assertEqual(payload['data']['reviewDecisionID'], str(rejected.pk))
        self.assertEqual(payload['data']['route'], f'/realm-review/{rejected.pk}')
        self.assertNotIn('Outside our topic', str(payload))
        self.assertNotIn('Hidden caption', str(payload))

    def test_invite_review_rejection_settles_pending_receipts(self):
        invite = Invite.objects.create(realm=self.realm, created_by=self.author, target_entity=self.viewer,
            kind='invite', status='for_approval')
        submitted = Approvals.objects.create(realm=self.realm, actor=self.author, target_kind='invite',
            target_id=str(invite.pk), action='submit')
        from community.invite_review import notify_invite_review_activity, transition_invite
        notify_invite_review_activity(submitted.pk)
        with self.captureOnCommitCallbacks(execute=True):
            transition_invite(actor=self.actor, invite_id=invite.pk, action='reject', expected_status='for_approval')
        self.assertTrue(next(n for n in self.memory.rows if n.type == 'community_invite_pending').referenceStatus)

    def test_withdrawing_a_pending_post_resolves_its_unread_receipts(self):
        self.deliver()
        from newsfeed.views import NewsfeedView
        request = APIRequestFactory().delete('/newsfeed', {'post_ids': [self.post.pk]}, format='json')
        request.entity = self.author
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch('newsfeed.views.publish_media_release'):
            response = NewsfeedView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(n.isRead for n in self.memory.rows if n.type == inbox.ADMIN_INBOX))

    def test_withdrawal_exception_never_allows_other_authors_or_interaction_deletes(self):
        from newsfeed.views import NewsfeedView
        from community.post_access import CommunityPostAccessMixin
        from rest_framework.views import APIView
        from rest_framework.response import Response
        class Interaction(CommunityPostAccessMixin, APIView):
            def delete(self, request):
                return Response({'status': True})
        for view, actor in ((NewsfeedView, self.reviewer), (Interaction, self.author)):
            request = APIRequestFactory().delete('/post', {'post_ids': [self.post.pk]}, format='json')
            request.entity = actor
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
            self.assertEqual(view.as_view()(request).status_code, 404)
        self.post.refresh_from_db()
        self.assertIsNone(self.post.deleted_at)

    def test_state_invalidation_contains_no_hidden_content_and_uses_current_recipients(self):
        from community.post_events import notify_post_state_changed
        with patch.object(inbox.RedisPubSubClient, 'publish_json') as publish:
            notify_post_state_changed(self.post.pk)
        recipients = {call.args[0] for call in publish.call_args_list}
        self.assertEqual(recipients, {f'events_{entity.pk}' for entity in (self.actor, self.author, self.reviewer, self.viewer)})
        for call in publish.call_args_list:
            event = call.args[1]
            self.assertEqual(event['event'], 'community_posts_changed')
            signal = event['message']['result']
            self.assertEqual(signal['target_id'], self.post.pk)
            self.assertEqual(set(signal), {'realm_id', 'target_id', 'approval', 'content_version', 'is_archived'})
            self.assertNotIn('Hidden caption', str(event))
