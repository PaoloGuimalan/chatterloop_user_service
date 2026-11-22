"""Real row-lock behavior; runner must provide a disposable fixture schema."""
import threading
import unittest
from types import SimpleNamespace

from django.apps import apps
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.test import override_settings
from django.conf import settings
from django.utils.timezone import now

from community.access import effective_realm_access, lock_realm_scope
from community.models import Member, Realm
from entity.models import Entity
from entity.roles import get_default_role
from entity.tests.permission_fixtures import create_permission


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires explicit disposable PostgreSQL fixtures')
class LifecycleConcurrencyTests(TransactionTestCase):
    def test_single_realm_query_does_not_walk_unrelated_realm_ancestry(self):
        from community.access import active_realms
        Entity.objects.bulk_create([Entity(id=f'other-entity-{i}',type='realm') for i in range(1000)])
        Realm.objects.bulk_create([Realm(id=f'other-{i}',realm_id=f'other-{i}',entity_id=f'other-entity-{i}',type='server',created_by=self.actor,name='Other') for i in range(1000)])
        query=active_realms(Realm.objects.filter(pk=self.child.pk))
        sql,params=query.query.sql_with_params()
        with connection.cursor() as cursor:
            cursor.execute('EXPLAIN (ANALYZE, FORMAT JSON) '+sql,params)
            plan=cursor.fetchone()[0][0]['Plan']
        def scans(node):
            yield node
            for child in node.get('Plans',[]):yield from scans(child)
        read=[node.get('Actual Rows',0)*node.get('Actual Loops',0) for node in scans(plan) if node.get('Relation Name')=='community_realm']
        self.assertLess(max(read,default=0),10,plan)
        self.assertEqual(list(query.values_list('pk',flat=True)),[self.child.pk])
    @override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS,'django.contrib.admin.apps.SimpleAdminConfig'])
    def test_staff_snapshot_cannot_overwrite_concurrent_deletion_intent(self):
        from django.contrib.admin.sites import AdminSite
        from community.admin import RealmAdmin
        admin=RealmAdmin(Realm,AdminSite())
        attempted=threading.Event();finished=threading.Event();errors=[]
        def delete():
            close_old_connections()
            try:
                with transaction.atomic():
                    attempted.set();lock_realm_scope(self.server.pk,exclusive=True)
                    Realm.objects.filter(pk=self.server.pk).update(is_active=False,deletion_requested_at=now())
                finished.set()
            except Exception as error:errors.append(error)
            finally:connection.close()
        with transaction.atomic():
            stale=admin.get_queryset(SimpleNamespace(method='POST')).get(pk=self.server.pk)
            thread=threading.Thread(target=delete);thread.start();self.assertTrue(attempted.wait(5))
            completed_while_editing=finished.wait(.2)
            stale.name='Staff edit';stale.save()
        thread.join(10)
        self.assertEqual(errors,[])
        self.assertFalse(completed_while_editing)
        self.server.refresh_from_db();self.assertIsNotNone(self.server.deletion_requested_at)
    def setUp(self):
        self.actor = Entity.objects.create(type='user')
        self.server = self.make('server')
        self.child = self.make('channel', parent=self.server)

    def make(self, kind, **kwargs):
        realm = Realm.objects.create(entity=Entity.objects.create(type='realm'), name=kind,
            type=kind, created_by=self.actor, **kwargs)
        Member.objects.create(entity=self.actor, realm=realm, added_by=self.actor,
                              role=get_default_role('owner', realm=realm))
        return realm

    def test_parent_exclusive_lock_prevents_child_write_after_deactivation(self):
        attempted = threading.Event()
        result, errors = [], []

        def write():
            close_old_connections()
            try:
                with transaction.atomic():
                    attempted.set()
                    path = lock_realm_scope(self.child.pk, exclusive=False)
                    result.append(effective_realm_access(path[-1]).allowed)
            except Exception as error:
                errors.append(error)
            finally:
                connection.close()

        with transaction.atomic():
            lock_realm_scope(self.server.pk, exclusive=True)
            Realm.objects.filter(pk=self.server.pk).update(is_active=False, deactivated_at=now())
            thread = threading.Thread(target=write)
            thread.start()
            self.assertTrue(attempted.wait(5))
            self.assertEqual(result, [])
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result, [False])

    def test_page_role_revoked_while_confirmation_waits_is_rechecked(self):
        from importlib import import_module
        from community.lifecycle import reactivate_realm
        from rest_framework.exceptions import PermissionDenied
        import_module('entity.migrations.0030_realm_lifecycle_permissions').seed_lifecycle_permissions(
            apps, SimpleNamespace(connection=connection))
        create_permission(code='realm.identity.act', scope='realm', realm_types=['page'])
        page = self.make('page')
        Realm.objects.filter(pk=self.child.pk).update(is_active=False, deactivated_at=now())
        Member.objects.filter(entity=self.actor, realm=self.child).delete()
        Member.objects.create(entity=page.entity, realm=self.child, added_by=self.actor,
                              role=get_default_role('owner', realm=self.child))
        account = SimpleNamespace(entity_id=self.actor.pk, entity=self.actor, is_active=True)
        entered = threading.Event()
        result = []

        def confirm():
            close_old_connections()
            try:
                entered.set()
                reactivate_realm(account=account, realm_id=self.child.pk, actor_entity_id=page.entity_id)
                result.append('allowed')
            except PermissionDenied:
                result.append('denied')
            except Exception as error:
                result.append(type(error).__name__)
            finally:
                connection.close()

        with transaction.atomic():
            lock_realm_scope(page.pk, exclusive=True)
            thread = threading.Thread(target=confirm)
            thread.start()
            self.assertTrue(entered.wait(5))
            membership = Member.objects.get(entity=self.actor, realm=page)
            membership.role = get_default_role('member', realm=page)
            membership.save()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ['denied'])
        self.child.refresh_from_db()
        self.assertFalse(self.child.is_active)
