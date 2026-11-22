"""Voluntary recovery must never revive restricted/deleting realms."""
from importlib import import_module, util
from types import SimpleNamespace
from unittest import TestCase

from django.utils.timezone import now
from django.forms import modelform_factory


class LifecycleStateTests(TestCase):
    def policy(self):
        self.assertIsNotNone(util.find_spec('community.lifecycle_state'),
                             'Lifecycle state policy is not implemented')
        return import_module('community.lifecycle_state')

    def realm(self, **values):
        return SimpleNamespace(**{'is_active': True, 'deactivated_at': None,
                                  'deletion_requested_at': None, **values})

    def test_legacy_inactive_is_not_recoverable(self):
        policy = self.policy()
        realm = self.realm(is_active=False)
        self.assertFalse(policy.voluntarily_deactivated(realm))
        self.assertEqual(policy.lifecycle_state(realm), 'restricted')

    def test_voluntary_inactive_is_recoverable(self):
        policy = self.policy()
        realm = self.realm(is_active=False, deactivated_at=now())
        self.assertTrue(policy.voluntarily_deactivated(realm))
        self.assertEqual(policy.lifecycle_state(realm), 'deactivated')

    def test_delete_marker_overrides_active(self):
        policy = self.policy()
        realm = self.realm(deactivated_at=now(), deletion_requested_at=now())
        self.assertTrue(policy.deletion_pending(realm))
        self.assertFalse(policy.voluntarily_deactivated(realm))
        self.assertEqual(policy.lifecycle_state(realm), 'deleting')

    def test_active_timestamp_is_not_permission_to_recover(self):
        policy = self.policy()
        realm = self.realm(deactivated_at=now())
        self.assertFalse(policy.voluntarily_deactivated(realm))
        self.assertEqual(policy.lifecycle_state(realm), 'active')

    def test_staff_restriction_clears_voluntary_recovery(self):
        self.assertIsNotNone(util.find_spec('community.lifecycle_forms'),
                             'Staff lifecycle form is not implemented')
        from community.models import Realm
        Form = import_module('community.lifecycle_forms').RealmLifecycleForm
        realm = Realm(id='inactive', name='Dormant', type='community', is_active=False,
                      deactivated_at=now())
        form = modelform_factory(Realm, form=Form, fields=['is_active'])({'is_active': False}, instance=realm)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['deactivated_at'])

    def test_staff_form_cannot_reactivate_pending_deletion(self):
        self.assertIsNotNone(util.find_spec('community.lifecycle_forms'),
                             'Staff lifecycle form is not implemented')
        from community.models import Realm
        Form = import_module('community.lifecycle_forms').RealmLifecycleForm
        realm = Realm(id='deleting', name='Gone', type='community', is_active=False,
                      deletion_requested_at=now())
        form = modelform_factory(Realm, form=Form, fields=['is_active'])({'is_active': True}, instance=realm)
        self.assertFalse(form.is_valid())
        self.assertIn('is_active', form.errors)
