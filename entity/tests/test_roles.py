from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from community.models import Member, Realm
from entity.models import Entity, EntityPermission, PermissionCatalogEntry
from entity.services.permission_catalog_cache import get_role_matrix
from entity.services.permission_resolver import has_permission
from entity.roles import get_default_role, resolve_role


class RoleTests(TestCase):
    def setUp(self):
        self.Role = apps.get_model("entity", "Role")
        self.cache = {}
        for name, effect in (
            ("_redis_get", self.cache.get),
            ("_redis_set", self.cache.__setitem__),
            ("_redis_delete", lambda key: self.cache.pop(key, None)),
        ):
            mock = patch(f"entity.services.permission_catalog_cache.{name}", side_effect=effect)
            mock.start()
            self.addCleanup(mock.stop)
        self.permission = PermissionCatalogEntry.objects.get(codename="realm.update")
        self.other = PermissionCatalogEntry.objects.get(codename="realm.member.view")

    def test_one_role_has_multiple_permissions_and_duplicate_names_are_allowed(self):
        role = self.Role.objects.create(name="reviewer")
        role.permissions.add(self.permission, self.other)
        role.permissions.add(self.permission)
        self.assertEqual(self.Role._meta.db_table, "entity_role")
        self.assertEqual(role.permissions.count(), 2)
        self.assertEqual(set(get_role_matrix()[str(role.pk)]), {"realm.update", "realm.member.view"})
        self.Role.objects.create(name="reviewer")
        self.assertEqual(self.Role.objects.filter(name="reviewer").count(), 2)

    def test_admin_role_name_is_text_and_accepts_custom_duplicate_names(self):
        from django import forms
        from entity.forms import RoleForm

        for _ in range(2):
            form = RoleForm(data={"name": "content reviewer", "permissions": [self.permission.pk]})
            self.assertIsInstance(form.fields["name"].widget, forms.TextInput)
            self.assertTrue(form.is_valid(), form.errors)
            role = form.save()
            self.assertEqual(role.name, "content reviewer")
            self.assertEqual(list(role.permissions.values_list("codename", flat=True)), ["realm.update"])
        self.assertEqual(self.Role.objects.filter(name="content reviewer").count(), 2)

    def test_admin_role_form_saves_multiple_permissions_and_names_remain_distinguishable(self):
        from entity.forms import RoleForm

        form = RoleForm(data={"name": "admin", "permissions": [self.permission.pk, self.other.pk]})
        self.assertTrue(form.is_valid(), form.errors)
        first = form.save()
        second = self.Role.objects.create(name="admin")
        self.assertNotEqual(str(first), str(second))
        self.assertEqual(set(first.permissions.values_list("codename", flat=True)), {"realm.update", "realm.member.view"})
        self.assertEqual(second.permissions.count(), 0)

    def test_unmarked_role_name_requires_its_exact_id(self):
        role = self.Role.objects.create(name="reviewer")
        with self.assertRaises(ValueError):
            resolve_role("reviewer")
        self.assertEqual(resolve_role(role_id=role.pk), role)

    def test_custom_role_defaults_and_entity_override_are_resolved(self):
        role = self.Role.objects.create(name="reviewer")
        role.permissions.add(self.permission)
        entity = Entity.objects.create(type="user")
        realm = Realm.objects.create(entity=Entity.objects.create(type="realm"),
                                     name="Reviewers", created_by=entity, type="community")
        Member.objects.create(entity=entity, realm=realm, added_by=entity, role=role)
        self.assertTrue(has_permission(entity, "realm.update", realm))
        EntityPermission.objects.create(entity=entity, realm=realm, permission=self.permission, effect="deny")
        self.assertFalse(has_permission(entity, "realm.update", realm))

    def test_add_remove_clear_and_reverse_add_invalidate_cached_matrix(self):
        role = self.Role.objects.create(name="reviewer")
        get_role_matrix()
        role.permissions.add(self.permission)
        self.assertEqual(get_role_matrix()[str(role.pk)], ["realm.update"])
        role.permissions.remove(self.permission)
        self.assertNotIn("realm.update", get_role_matrix().get(str(role.pk), []))
        self.permission.roles.add(role)
        self.assertEqual(get_role_matrix()[str(role.pk)], ["realm.update"])
        role.permissions.clear()
        self.assertNotIn("realm.update", get_role_matrix().get(str(role.pk), []))

    def test_rename_direct_through_changes_and_disabled_permissions_refresh_matrix(self):
        role = self.Role.objects.create(name="reviewer")
        through = self.Role.permissions.through
        get_role_matrix()
        link = through.objects.create(role=role, permission=self.permission)
        self.assertEqual(get_role_matrix()[str(role.pk)], ["realm.update"])
        role.name = "content-reviewer"
        role.save()
        matrix = get_role_matrix()
        self.assertEqual(matrix[str(role.pk)], ["realm.update"])
        self.permission.is_active = False
        self.permission.save()
        self.assertNotIn("realm.update", get_role_matrix().get(str(role.pk), []))
        link.delete()
        self.assertNotIn("realm.update", get_role_matrix().get(str(role.pk), []))

    def test_same_named_roles_have_independent_permissions_by_id(self):
        first = self.Role.objects.create(name="moderator")
        second = self.Role.objects.create(name="moderator")
        first.permissions.add(self.permission)
        second.permissions.add(self.other)
        creator = Entity.objects.create(type="user")
        realm = Realm.objects.create(entity=Entity.objects.create(type="realm"),
                                     name="Separate roles", created_by=creator, type="community")
        for role, can_update, can_view in ((first, True, False), (second, False, True)):
            entity = Entity.objects.create(type="user")
            Member.objects.create(entity=entity, realm=realm, added_by=creator, role=role)
            self.assertEqual(has_permission(entity, "realm.update", realm), can_update)
            self.assertEqual(has_permission(entity, "realm.member.view", realm), can_view)
        self.assertEqual(resolve_role(role_id=second.pk), second)
        self.assertEqual(resolve_role("moderator"), get_default_role("moderator"))


class RoleMigrationTests(TransactionTestCase):
    before = [("entity", "0022_entitypermission_catalog")]
    after = [("entity", "0024_catalog_entity_types"), ("community", "0011_role_references")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_existing_role_grants_are_grouped_without_losing_links_and_reverse_cleanly(self):
        old = self.migrate(self.before)
        self.addCleanup(self.migrate, self.after)
        catalog = old.get_model("entity", "PermissionCatalogEntry")
        first, _ = catalog.objects.get_or_create(codename="test.role.first", defaults={"scope": "realm"})
        second, _ = catalog.objects.get_or_create(codename="test.role.second", defaults={"scope": "realm"})
        links = old.get_model("entity", "RolePermission")
        for role, permission in (("reviewer", first), ("reviewer", second), ("admin", first)):
            links.objects.create(role=role, permission=permission)
        original = list(links.objects.order_by("id").values_list("id", "role", "permission_id", "created_at"))
        current = self.migrate(self.after)
        roles = current.get_model("entity", "Role")
        self.assertEqual(roles.objects.filter(name="reviewer").count(), 1)
        reviewer = roles.objects.get(name="reviewer")
        self.assertEqual(set(reviewer.permissions.values_list("codename", flat=True)), {"test.role.first", "test.role.second"})
        migrated = current.get_model("entity", "RolePermission")
        self.assertEqual(list(migrated.objects.order_by("id").values_list(
            "id", "role__name", "permission_id", "created_at")), original)
        old = self.migrate(self.before)
        self.assertEqual(list(old.get_model("entity", "RolePermission").objects.order_by("id").values_list(
            "id", "role", "permission_id", "created_at")), original)
