"""Run the checked-in Node audience SQL on the same ORM fixture as Django."""
import json
from pathlib import Path
import subprocess
from unittest import skipUnless
from django.db import connection
from django.test import TestCase
from community.models import Approvals, Member, Type
from community.notification_permissions import permitted_members
from entity.models import Entity, EntityPermission, Role
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture
from newsfeed.models import Post


class NotificationSQLContractTests(RealmRolePolicyFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(__file__).resolve().parents[4] / 'server'
        code = """
const {memberHasAnyPermissionSQL}=require('./reusables/models/communityNotificationPermissions');
const {notificationDecisionProofs}=require('./reusables/models/communityNotifications');
(async()=>{
let decision;
await notificationDecisionProofs({query:async sql=>{decision=sql;return {rows:[]};}},
  {distinct:async()=>['cb48b3df-bd09-44b5-b218-b47f1afce820']},'actor');
process.stdout.write(JSON.stringify({
permission:`SELECT m.entity_id FROM community_member m JOIN entity_role r ON r.id=m.role_id WHERE m.realm_id=$1 AND ${memberHasAnyPermissionSQL('m','r',['realm.post.review'])}`,
decision}));})();
"""
        cls.sql = json.loads(subprocess.check_output(['node', '--preserve-symlinks', '--preserve-symlinks-main', '-e', code], cwd=root, text=True))

    def reviewers(self):
        sql = self.sql['permission'].replace('$1', '%s').replace('NOW()', 'CURRENT_TIMESTAMP')
        with connection.cursor() as cursor:
            cursor.execute(sql, [str(self.realm.pk)])
            node = {str(row[0]) for row in cursor.fetchall()}
        django = set(map(str, permitted_members(self.realm, ['realm.post.review']).values_list('entity_id', flat=True)))
        self.assertEqual(node, django)
        return node

    @skipUnless(connection.vendor == 'postgresql', 'Relational audience guard runs on the disposable PostgreSQL suite')
    def test_current_local_grants_overrides_activity_and_owner_match_between_runtimes(self):
        role = Role.objects.create(realm=self.realm, name='Reviewer')
        role.permissions.add(self.catalog['realm.post.review'])
        member = Member.objects.create(realm=self.realm, role=role, entity=Entity.objects.create(type='user'), added_by=self.actor)
        self.assertEqual(self.reviewers(), {str(self.actor.pk), str(member.entity_id)})
        override = EntityPermission.objects.create(realm=self.realm, entity=member.entity,
            permission=self.catalog['realm.post.review'], effect='deny')
        self.assertEqual(self.reviewers(), {str(self.actor.pk)})
        override.delete()
        Member.objects.filter(pk=member.pk).update(role=self.other.owned_roles.get(code='owner'))
        self.assertEqual(self.reviewers(), {str(self.actor.pk)})
        self.catalog['realm.post.review'].is_active = False
        self.catalog['realm.post.review'].save(update_fields=['is_active'])
        self.assertEqual(self.reviewers(), set())

    def test_decision_exception_proves_true_author_and_reviewer_is_not_a_recipient(self):
        post = Post.objects.create(entity=self.actor, file_type='text', content_type='text', approval=2)
        decision = Approvals.objects.create(realm=self.realm, actor=self.other.entity,
            target_kind='post', target_id=post.pk, action='reject')
        sql = self.sql['decision'].replace('$1', '%s').replace('$2', '%s')
        decision_param = [str(decision.pk)]
        if connection.vendor == 'sqlite':
            sql = sql.replace('d.id::text', 'd.id').replace('ANY(%s::uuid[])', '%s')
            decision_param = decision.pk.hex
        def recipients(actor):
            with connection.cursor() as cursor:
                cursor.execute(sql, [str(actor.pk), decision_param])
                return cursor.fetchall()
        self.assertEqual(len(recipients(self.actor)), 1)
        self.assertEqual(recipients(self.other.entity), [])
        post.delete()
        self.assertEqual(recipients(self.actor), [])

    @skipUnless(connection.vendor == 'postgresql', 'Relational audience guard runs on the disposable PostgreSQL suite')
    def test_inapplicable_or_malformed_metadata_denies_even_owner_audiences(self):
        entry = self.catalog['realm.post.review']
        for kinds in ([], ['page']):
            entry.realm_types.set(Type.objects.filter(code__in=kinds))
            self.assertEqual(self.reviewers(), set())
        entry.realm_types.set(Type.objects.filter(code__in=['community', 'page']))
        self.assertEqual(self.reviewers(), {str(self.actor.pk)})
