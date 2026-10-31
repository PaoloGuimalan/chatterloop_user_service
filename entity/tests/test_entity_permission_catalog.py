from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils.timezone import now

from entity.models import Entity, EntityPermission, PermissionCatalogEntry
from entity.services.permission_resolver import has_permission


class EntityPermissionCatalogTests(TestCase):
    def setUp(self):
        for method in ("_redis_get", "_redis_set", "_redis_delete"):
            mock = patch(f"entity.services.permission_catalog_cache.{method}", return_value=None)
            mock.start()
            self.addCleanup(mock.stop)
        self.entity = Entity.objects.create(type="user")
        self.catalog = PermissionCatalogEntry.objects.create(
            codename="test.explicit", scope="global",
        )

    def test_override_references_catalog_by_numeric_id(self):
        field = EntityPermission._meta.get_field("permission")
        self.assertTrue(field.many_to_one)
        self.assertEqual(field.column, "permission_id")
        self.assertEqual(field.target_field.name, "id")
        row = EntityPermission.objects.create(
            entity=self.entity, permission=self.catalog, effect="grant",
        )
        self.assertEqual(row.permission_id, self.catalog.pk)
        self.assertEqual(row.permission.codename, "test.explicit")
        self.assertTrue(has_permission(self.entity, "test.explicit"))

    def test_database_rejects_missing_catalog_id(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EntityPermission.objects.create(
                entity=self.entity, permission_id=999999999, effect="grant",
            )
            connection.check_constraints(table_names=[EntityPermission._meta.db_table])

    def test_catalog_deletion_cannot_silently_remove_override(self):
        EntityPermission.objects.create(
            entity=self.entity, permission=self.catalog, effect="deny",
        )
        with self.assertRaises(ProtectedError):
            self.catalog.delete()
        self.assertFalse(has_permission(self.entity, "test.explicit"))

    def test_raw_sql_uses_permission_id_and_old_column_is_absent(self):
        self.assertEqual(PermissionCatalogEntry._meta.db_table, "entity_permission_catalog")
        with connection.cursor() as cursor:
            columns = {col.name for col in connection.introspection.get_table_description(
                cursor, "entity_permission",
            )}
        self.assertIn("permission_id", columns)
        self.assertNotIn("permission", columns)
        self.assertNotIn("legacy_permission", columns)


class EntityPermissionMigrationTests(TransactionTestCase):
    before = [("entity", "0021_messages_encryption_purpose")]
    after = [("entity", "0024_catalog_entity_types"), ("community", "0011_role_references")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        self.current_apps = executor.loader.project_state(targets).apps
        return self.current_apps

    def setUp(self):
        for method in ("_redis_get", "_redis_set", "_redis_delete"):
            mock = patch(f"entity.services.permission_catalog_cache.{method}", return_value=None)
            mock.start()
            self.addCleanup(mock.stop)
        self.old_apps = self.migrate(self.before)
        self.addCleanup(self.restore_schema)
        self.entity = self.old_apps.get_model("entity", "Entity").objects.create(type="user")
        self.catalog = self.old_apps.get_model("entity", "PermissionCatalogEntry")
        self.overrides = self.old_apps.get_model("entity", "EntityPermission")
        self.realm = self.old_apps.get_model("community", "Realm").objects.create(
            id="migration-realm", realm_id="migration-realm", name="Migration",
            type="community", entity=self.old_apps.get_model("entity", "Entity").objects.create(type="realm"),
            created_by=self.entity,
        )

    def restore_schema(self):
        # An intentionally ambiguous legacy fixture must not block later tests.
        current = self.current_apps.get_model("entity", "EntityPermission")
        if not current._meta.get_field("permission").is_relation:
            current.objects.filter(permission="test.ambiguous").delete()
        self.migrate(self.after)

    def test_existing_records_and_inactive_catalog_survive_forward_and_backward(self):
        entry = self.catalog.objects.create(codename="test.existing", scope="realm", is_active=False)
        row = self.overrides.objects.create(
            entity=self.entity, permission="test.existing", effect="deny", realm=self.realm,
            reason="Keep restriction", created_by=self.entity, expires_at=now() + timedelta(days=3),
        )
        fields = ("id", "entity_id", "effect", "realm_id", "reason", "created_by_id", "created_at", "expires_at")
        original = self.overrides.objects.values(*fields).get(pk=row.pk)
        apps = self.migrate(self.after)
        rows = apps.get_model("entity", "EntityPermission")
        self.assertEqual(rows.objects.values(*fields).get(pk=row.pk), original)
        self.assertEqual(rows.objects.get(pk=row.pk).permission_id, entry.pk)
        self.assertFalse(apps.get_model("entity", "PermissionCatalogEntry").objects.get(pk=entry.pk).is_active)
        apps = self.migrate(self.before)
        restored = apps.get_model("entity", "EntityPermission").objects.get(pk=row.pk)
        self.assertEqual(restored.permission, "test.existing")
        self.assertEqual(apps.get_model("entity", "EntityPermission").objects.values(*fields).get(pk=row.pk), original)

    def test_uncataloged_codes_are_recovered_and_existing_overrides_remain_usable(self):
        fixtures = [("test.recovered.global", None, "grant"), ("test.recovered.realm", self.realm, "deny")]
        ids = []
        for code, realm, effect in fixtures:
            ids.append(self.overrides.objects.create(
                entity=self.entity, permission=code, effect=effect, realm=realm,
                expires_at=now() - timedelta(days=1),
            ).pk)
        apps = self.migrate(self.after)
        catalog = apps.get_model("entity", "PermissionCatalogEntry")
        rows = apps.get_model("entity", "EntityPermission")
        for row_id, (code, realm, effect) in zip(ids, fixtures):
            entry = catalog.objects.get(codename=code)
            self.assertEqual(entry.scope, "realm" if realm else "global")
            self.assertTrue(entry.is_active)
            self.assertEqual(rows.objects.get(pk=row_id).permission_id, entry.pk)
            self.assertEqual(rows.objects.get(pk=row_id).effect, effect)
        self.migrate(self.before)
        self.assertEqual(set(self.overrides.objects.filter(pk__in=ids).values_list("permission", flat=True)),
                         {"test.recovered.global", "test.recovered.realm"})

    def test_migrated_global_and_realm_grants_denies_and_expiry_still_resolve(self):
        for code, realm, effect, expired, expected in (
            ("test.global.grant", None, "grant", False, True),
            ("test.global.deny", None, "deny", False, False),
            ("test.realm.grant", self.realm, "grant", False, True),
            ("test.realm.deny", self.realm, "deny", False, False),
            ("test.expired.grant", None, "grant", True, False),
        ):
            self.overrides.objects.create(
                entity=self.entity, permission=code, effect=effect, realm=realm,
                expires_at=now() - timedelta(days=1) if expired else None,
            )
        self.migrate(self.after)
        entity = Entity.objects.get(pk=self.entity.pk)
        from community.models import Realm
        realm = Realm.objects.get(pk=self.realm.pk)
        for code, scope, expected in (
            ("test.global.grant", None, True),
            ("test.global.deny", None, False),
            ("test.realm.grant", realm, True),
            ("test.realm.deny", realm, False),
            ("test.expired.grant", None, False),
        ):
            with self.subTest(code=code):
                self.assertEqual(has_permission(entity, code, realm=scope), expected)

    def test_ambiguous_missing_scope_stops_without_changing_existing_data(self):
        for realm in (None, self.realm):
            self.overrides.objects.create(
                entity=self.entity, permission="test.ambiguous", effect="deny", realm=realm,
            )
        with self.assertRaisesRegex(RuntimeError, "test.ambiguous"):
            self.migrate(self.after)
        self.assertEqual(self.overrides.objects.filter(permission="test.ambiguous").count(), 2)
        self.assertFalse(self.catalog.objects.filter(codename="test.ambiguous").exists())
