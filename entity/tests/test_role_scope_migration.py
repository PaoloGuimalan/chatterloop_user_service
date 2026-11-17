from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RoleScopeMigrationTests(TransactionTestCase):
    before = [("entity", "0025_editable_definition_names"), ("community", "0011_role_references")]
    after = [("entity", "0026_role_identity_scope")]

    def migrate(self, targets=None):
        executor = MigrationExecutor(connection)
        targets = targets or executor.loader.graph.leaf_nodes()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_shared_definitions_grants_and_memberships_are_copied_per_realm(self):
        old = self.migrate(self.before)
        self.addCleanup(self.migrate)
        roles = old.get_model("entity", "Role")
        for name in ("owner", "admin", "moderator", "member"):
            roles.objects.get_or_create(name=name, is_default=True)
        custom = roles.objects.create(name="Post reviewer")
        duplicate = roles.objects.create(name="admin")
        permission = old.get_model("entity", "PermissionCatalogEntry").objects.create(
            codename="test.scope.review", scope="realm")
        old.get_model("entity", "RolePermission").objects.create(role=custom, permission=permission)
        entities = old.get_model("entity", "Entity")
        actor = entities.objects.create(type="user")
        realms = []
        ids = []
        for index, kind in enumerate(("community", "server", "page")):
            realm = old.get_model("community", "Realm").objects.create(name=f"Realm {index}",
                id=f"scope-realm-{index}", realm_id=f"scope-realm-{index}",
                type=kind, entity=entities.objects.create(type="realm"), created_by=actor)
            realms.append(realm)
            membership = old.get_model("community", "Member").objects.create(
                entity=actor, realm=realm, added_by=actor, role=custom)
            invite = old.get_model("community", "Invite").objects.create(
                realm=realm, created_by=actor, role=duplicate, purpose="manage")
            ids.append((membership.pk, invite.pk))
        current = self.migrate(self.after)
        roles = current.get_model("entity", "Role")
        self.assertFalse(roles.objects.filter(realm__isnull=True).exists())
        self.assertNotIn("base_role", {f.name for f in roles._meta.fields})
        members = current.get_model("community", "Member")
        invites = current.get_model("community", "Invite")
        local_ids = []
        for realm, (member_id, invite_id) in zip(realms, ids):
            local = roles.objects.filter(realm_id=realm.pk)
            self.assertEqual(set(local.filter(is_default=True).values_list("code", flat=True)),
                             {"owner", "admin", "moderator", "member"})
            self.assertEqual(local.values("code").distinct().count(), local.count())
            reviewer = local.get(code="post-reviewer")
            self.assertEqual(list(reviewer.permissions.values_list("pk", flat=True)), [permission.pk])
            self.assertEqual(members.objects.get(pk=member_id).role_id, reviewer.pk)
            self.assertEqual(invites.objects.get(pk=invite_id).role.realm_id, realm.pk)
            self.assertNotEqual(invites.objects.get(pk=invite_id).role.code, "admin")
            local_ids.append(reviewer.pk)
        self.assertEqual(len(set(local_ids)), len(realms))
