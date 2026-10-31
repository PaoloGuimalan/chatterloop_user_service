"""Execute backend override SQL against the migrated schema, without services."""

from datetime import timedelta
from pathlib import Path
import re

from django.db import connection
from django.test import TestCase
from django.utils.timezone import now
from unittest.mock import patch

from community.models import Realm
from entity.models import Entity, EntityPermission, PermissionCatalogEntry
from entity.roles import get_default_role


class PermissionSQLContractTests(TestCase):
    def setUp(self):
        for method in ("_redis_get", "_redis_set", "_redis_delete"):
            mock = patch(f"entity.services.permission_catalog_cache.{method}", return_value=None)
            mock.start()
            self.addCleanup(mock.stop)
        self.entity = Entity.objects.create(type="user")
        self.other = Entity.objects.create(type="user")
        self.realm = Realm.objects.create(
            entity=Entity.objects.create(type="realm"), created_by=self.entity,
            name="SQL contracts", type="community",
        )
        self.catalog = PermissionCatalogEntry.objects.get(codename="messages.read")

    def query(self, backend, codename="messages.read", realm=None):
        root = Path(__file__).resolve().parents[4]
        if backend == "node":
            filename = root / "server/reusables/hooks/permissionChecker.js"
            marker = "async function _activeOverride("
        else:
            filename = root / "chatterloop_services/developer_service/internal/auth/token.go"
            marker = "func hasExplicitGrant("
        sql = filename.read_text(encoding="utf-8").split(marker, 1)[1].split("`", 2)[1]
        # SQLite has the same joins/predicates; adapt only the driver parameters
        # and clock spelling. This exercises the actual checked-in backend SQL.
        sql = re.sub(r"\$[123]", "%s", sql).replace("NOW()", "CURRENT_TIMESTAMP")
        params = [str(self.entity.pk), codename]
        if backend == "node":
            params.append(realm.pk if realm else None)
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [row[0] for row in cursor.fetchall()]

    def test_backends_resolve_grants_and_denies_by_catalog_id(self):
        for effect in ("grant", "deny"):
            row = EntityPermission.objects.create(
                entity=self.entity, permission=self.catalog, effect=effect,
            )
            for backend in ("node", "go"):
                with self.subTest(effect=effect, backend=backend):
                    self.assertEqual(self.query(backend), [effect])
            row.delete()

    def test_backends_ignore_other_entities_expired_overrides_and_other_permissions(self):
        EntityPermission.objects.create(entity=self.other, permission=self.catalog, effect="grant")
        EntityPermission.objects.create(
            entity=self.entity, permission=self.catalog, effect="deny", expires_at=now() - timedelta(days=1),
        )
        EntityPermission.objects.create(
            entity=self.entity, permission=PermissionCatalogEntry.objects.get(codename="notifications.read"),
            effect="grant",
        )
        for backend in ("node", "go"):
            with self.subTest(backend=backend):
                self.assertEqual(self.query(backend), [])

    def test_node_realm_overrides_do_not_apply_globally_or_to_another_realm(self):
        permission = PermissionCatalogEntry.objects.get(codename="realm.update")
        EntityPermission.objects.create(
            entity=self.entity, permission=permission, realm=self.realm, effect="deny",
        )
        self.assertEqual(self.query("node", "realm.update", self.realm), ["deny"])
        self.assertEqual(self.query("node", "realm.update"), [])
        self.assertEqual(self.query("go", "realm.update"), [])

    def test_go_rejects_inactive_catalog_grants(self):
        EntityPermission.objects.create(entity=self.entity, permission=self.catalog, effect="grant")
        self.catalog.is_active = False
        self.catalog.save(update_fields=["is_active"])
        self.assertEqual(self.query("go"), [])

    def test_node_catalog_and_default_matrix_sql_read_the_renamed_table(self):
        root = Path(__file__).resolve().parents[4]
        source = (root / "server/reusables/hooks/permissionChecker.js").read_text(encoding="utf-8")
        for function, expected in (
            ("getCatalog", ("messages.read", "global", True)),
            ("getRoleMatrix", (get_default_role("owner").pk, "realm.update")),
            ("getEntityTypeMatrix", ("user", "module.diary.access")),
        ):
            with self.subTest(function=function), connection.cursor() as cursor:
                sql = source.split(f"async function {function}(", 1)[1].split("`", 2)[1]
                cursor.execute(sql)
                self.assertIn(expected, cursor.fetchall())
