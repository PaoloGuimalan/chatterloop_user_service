from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from entity.models import Entity, PermissionCatalogEntry
from entity.services.permission_catalog_cache import get_entity_type_matrix
from entity.services.permission_resolver import has_permission


class CatalogEntityTypesTests(TestCase):
    def setUp(self):
        self.Type = apps.get_model("entity", "EntityTypeDefinition")
        self.cache = {}
        for name, effect in (("_redis_get", self.cache.get), ("_redis_set", self.cache.__setitem__),
                             ("_redis_delete", lambda key: self.cache.pop(key, None))):
            mock = patch(f"entity.services.permission_catalog_cache.{name}", side_effect=effect)
            mock.start()
            self.addCleanup(mock.stop)
        self.permission = PermissionCatalogEntry.objects.get(codename="module.diary.access")

    def test_catalog_owns_entity_types_m2m_and_old_table_is_absent(self):
        field = PermissionCatalogEntry._meta.get_field("entity_types")
        self.assertTrue(field.many_to_many)
        tables = connection.introspection.table_names()
        self.assertIn("entity_permission_catalog_entity_types", tables)
        self.assertNotIn("entity_entitytypedefaultpermission", tables)
        self.assertEqual(set(self.permission.entity_types.values_list("name", flat=True)), {"user"})

    def test_type_assignments_keep_defaults_and_update_cached_permissions(self):
        user = Entity.objects.create(type="user")
        realm = Entity.objects.create(type="realm")
        self.assertTrue(has_permission(user, "module.diary.access"))
        self.assertFalse(has_permission(realm, "module.diary.access"))
        self.permission.entity_types.add(self.Type.objects.get(name="realm"))
        self.assertTrue(has_permission(realm, "module.diary.access"))
        self.permission.entity_types.remove(self.Type.objects.get(name="user"))
        self.assertFalse(has_permission(user, "module.diary.access"))
        self.permission.entity_types.clear()
        self.assertFalse(has_permission(realm, "module.diary.access"))

    def test_disabled_catalog_entries_do_not_grant_type_defaults(self):
        self.permission.is_active = False
        self.permission.save()
        self.assertNotIn("module.diary.access", get_entity_type_matrix().get("user", []))

    def test_admin_entity_type_name_is_text_and_accepts_custom_definitions(self):
        from django import forms

        form_class = forms.modelform_factory(self.Type, fields=("name",))
        form = form_class(data={"name": "organization"})
        self.assertIsInstance(form.fields["name"].widget, forms.TextInput)
        self.assertTrue(form.is_valid(), form.errors)
        definition = form.save()
        self.permission.entity_types.add(definition)
        self.assertIn("module.diary.access", get_entity_type_matrix()["organization"])

    def test_catalog_form_saves_entity_types_and_retains_existing_assignment_ids(self):
        from entity.forms import PermissionCatalogForm

        user = self.Type.objects.get(name="user")
        realm = self.Type.objects.get(name="realm")
        link = self.permission.entity_types.through.objects.get(permission=self.permission, entity_type=user)
        form = PermissionCatalogForm(instance=self.permission, data={
            "codename": "module.diary.access", "scope": "entity_type", "is_active": True,
            "description": "Personal diary", "entity_types": [user.pk, realm.pk],
        })
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(set(saved.entity_types.values_list("name", flat=True)), {"user", "realm"})
        self.assertEqual(saved.entity_types.through.objects.get(permission=saved, entity_type=user).pk, link.pk)


class CatalogEntityTypesMigrationTests(TransactionTestCase):
    before = [("entity", "0023_role_permissions"), ("community", "0011_role_references")]
    after = [("entity", "0024_catalog_entity_types"), ("community", "0011_role_references")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_existing_type_assignments_and_ids_survive_forward_and_reverse(self):
        old = self.migrate(self.before)
        self.addCleanup(self.migrate, self.after)
        catalog = old.get_model("entity", "PermissionCatalogEntry")
        permission = catalog.objects.create(codename="test.types", scope="entity_type", is_active=False)
        links = old.get_model("entity", "EntityTypeDefaultPermission")
        for name in ("user", "realm", "legacy-type"):
            links.objects.create(entity_type=name, permission=permission)
        original = list(links.objects.order_by("id").values_list("id", "entity_type", "permission_id", "created_at"))
        current = self.migrate(self.after)
        migrated = current.get_model("entity", "PermissionEntityType")
        self.assertEqual(list(migrated.objects.order_by("id").values_list(
            "id", "entity_type__name", "permission_id", "created_at")), original)
        self.assertFalse(current.get_model("entity", "PermissionCatalogEntry").objects.get(pk=permission.pk).is_active)
        old = self.migrate(self.before)
        self.assertEqual(list(old.get_model("entity", "EntityTypeDefaultPermission").objects.order_by("id").values_list(
            "id", "entity_type", "permission_id", "created_at")), original)
