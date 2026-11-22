from importlib import util
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from community.tests.test_lifecycle_api import LifecycleFixture


class LifecycleEventTests(LifecycleFixture, TestCase):
    def test_deletion_job_is_postcommit_and_publish_failure_preserves_intent(self):
        with patch('user_service.services.rabbitmq.RabbitMQClient.publish', return_value=False) as publish, \
             patch('user_service.services.redis.RedisPubSubClient.publish_json'):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.call('RealmDeleteView', data={'confirm_name': self.realm.name})
                publish.assert_not_called()
        self.assertEqual(response.status_code, 202)
        self.realm.refresh_from_db()
        self.assertIsNotNone(self.realm.deletion_requested_at)
        publish.assert_called_once_with('realm_deletion', {
            'realm_id': str(self.realm.pk), 'deletion_requested_at': self.realm.deletion_requested_at})

    def test_rolled_back_deletion_does_not_enqueue(self):
        with patch('user_service.services.rabbitmq.RabbitMQClient.publish') as publish:
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        self.call('RealmDeleteView', data={'confirm_name': self.realm.name})
                        raise ValueError('abort')
                except ValueError:
                    pass
        publish.assert_not_called()

    def test_postcommit_only_emits_content_free_invalidation(self):
        self.assertIsNotNone(util.find_spec('community.lifecycle_events'), 'Lifecycle dispatch is missing')
        frames = []
        with patch('user_service.services.redis.RedisPubSubClient.publish_json',
                   side_effect=lambda channel, frame: frames.append((channel, frame))):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.call('RealmDeactivateView')
                self.assertEqual(frames, [])
                self.assertEqual(response.status_code, 200)
        personal = [frame for channel, frame in frames if channel == f'events_{self.actor.pk}']
        self.assertEqual(len(personal), 1)
        self.assertEqual(personal[0]['event'], 'realm_lifecycle_changed')
        self.assertEqual(personal[0]['message']['result']['state'], 'deactivated')
        self.assertEqual(set(personal[0]['message']['result']), {'realm_id', 'state', 'occurred_at'})
        self.assertTrue(any(channel == 'SUB_realm_lifecycle' for channel, _ in frames))

    def test_rollback_does_not_publish(self):
        self.assertIsNotNone(util.find_spec('community.lifecycle_events'), 'Lifecycle dispatch is missing')
        frames = []
        with patch('user_service.services.redis.RedisPubSubClient.publish_json',
                   side_effect=lambda channel, frame: frames.append(frame)):
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        self.call('RealmDeactivateView')
                        raise ValueError('abort')
                except ValueError:
                    pass
        self.assertEqual(frames, [])
        self.realm.refresh_from_db()
        self.assertTrue(self.realm.is_active)

    def test_broker_failure_does_not_undo_committed_deactivation(self):
        self.assertIsNotNone(util.find_spec('community.lifecycle_events'), 'Lifecycle dispatch is missing')
        with patch('user_service.services.redis.RedisPubSubClient.publish_json', side_effect=OSError('offline')):
            with self.assertLogs('community.lifecycle_events', level='WARNING'):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.call('RealmDeactivateView')
        self.assertEqual(response.status_code, 200)
        self.realm.refresh_from_db()
        self.assertFalse(self.realm.is_active)
