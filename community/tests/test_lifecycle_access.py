from importlib import import_module, util

from django.test import TestCase
from django.utils.timezone import now

from community.models import Member, Realm
from entity.models import Entity
from entity.roles import get_default_role
from entity.services.permission_resolver import has_permission
from entity.tests.permission_fixtures import create_permission


class LifecycleAccessTests(TestCase):
    def setUp(self):
        self.actor = Entity.objects.create(type='user')
        create_permission(code='realm.update', scope='realm', realm_types=['server', 'channel'])
        self.parent = self.make('server')
        self.child = self.make('channel', parent=self.parent)

    def make(self, kind, **kwargs):
        realm = Realm.objects.create(name=kind, type=kind, created_by=self.actor,
            entity=Entity.objects.create(type='realm'), **kwargs)
        Member.objects.create(entity=self.actor, realm=realm, added_by=self.actor,
                              role=get_default_role('owner', realm=realm))
        return realm

    def access(self):
        self.assertIsNotNone(util.find_spec('community.access'), 'Effective realm access is missing')
        return import_module('community.access')

    def test_inactive_owner_denied_with_nonfresh_permission(self):
        self.parent.is_active = False
        self.parent.save(update_fields=['is_active'])
        self.assertFalse(has_permission(self.actor, 'realm.update', self.parent))

    def test_deleting_owner_denied_even_if_flag_is_active(self):
        self.parent.deletion_requested_at = now()
        self.parent.save(update_fields=['deletion_requested_at'])
        self.assertFalse(has_permission(self.actor, 'realm.update', self.parent, fresh=True))

    def test_active_child_cannot_bypass_inactive_parent(self):
        self.parent.is_active = False
        self.parent.save(update_fields=['is_active'])
        self.assertFalse(has_permission(self.actor, 'realm.update', self.child, fresh=True))

    def test_server_reactivation_preserves_independent_child_state(self):
        self.child.is_active = False
        self.child.save(update_fields=['is_active'])
        policy = self.access()
        self.assertTrue(policy.effective_realm_access(self.parent).allowed)
        self.assertFalse(policy.effective_realm_access(self.child).allowed)
        self.assertEqual(list(policy.active_realms(Realm.objects.all()).values_list('pk', flat=True)),
                         [self.parent.pk])

    def test_cyclic_hierarchy_is_unavailable_in_reads_and_lists(self):
        Realm.objects.filter(pk=self.parent.pk).update(parent=self.child)
        self.parent.refresh_from_db()
        policy = self.access()
        self.assertFalse(policy.effective_realm_access(self.parent).allowed)
        self.assertFalse(policy.active_realms(Realm.objects.all()).exists())

    def test_active_queryset_remains_lazy_and_hides_disabled_descendants(self):
        self.parent.is_active = False
        self.parent.save(update_fields=['is_active'])
        policy = self.access()
        with self.assertNumQueries(0):
            query = policy.active_realms(Realm.objects.all())
        self.assertEqual(list(query), [])
