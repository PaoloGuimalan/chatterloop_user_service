"""Execute backend override SQL against the migrated schema, without services."""

from datetime import timedelta
from pathlib import Path
import re

from django.db import connection
from django.test import TestCase
from django.utils.timezone import now
from unittest.mock import patch

from community.models import Member, Realm
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
            ("getRoleMatrix", (get_default_role("admin", realm=self.realm).pk, "realm.update")),
            ("getEntityTypeMatrix", ("user", "module.diary.access")),
        ):
            with self.subTest(function=function), connection.cursor() as cursor:
                sql = source.split(f"async function {function}(", 1)[1].split("await pool.query(", 1)[1].split("`", 2)[1]
                cursor.execute(sql.replace("$1", "%s"), [self.realm.pk] if function == "getRoleMatrix" else [])
                self.assertIn(expected, cursor.fetchall())

    def test_node_identity_guard_uses_current_scoped_grants_and_matches_django(self):
        from entity.services.acting import can_act_as_entity
        from types import SimpleNamespace
        root = Path(__file__).resolve().parents[4]
        source = (root / "server/reusables/models/actingIdentity.js").read_text(encoding="utf-8")
        sql = source.split("await client.query(`", 1)[1].split("`", 1)[0]
        sql = sql.replace("$1", "%s").replace("$2", "%s").replace("NOW()", "CURRENT_TIMESTAMP")
        self.realm.type = "page"
        self.realm.save(update_fields=["type"])
        role = get_default_role("admin", realm=self.realm)
        member = Member.objects.create(entity=self.entity, realm=self.realm, role=role, added_by=self.entity)
        permission = PermissionCatalogEntry.objects.get(codename="realm.identity.act")
        account = SimpleNamespace(entity_id=self.entity.pk)
        def check(expected):
            with connection.cursor() as cursor:
                cursor.execute(sql, [str(self.entity.pk), str(self.realm.entity_id)])
                self.assertEqual(bool(cursor.fetchall()), expected)
            self.assertEqual(can_act_as_entity(account, self.realm.entity), expected)
        check(False)
        role.permissions.add(permission)
        check(True)
        EntityPermission.objects.create(entity=self.entity, realm=self.realm, permission=permission, effect="deny")
        check(False)
        Member.objects.filter(pk=member.pk).update(role=get_default_role("owner", realm=self.realm))
        check(True)
        permission.is_active = False
        permission.save(update_fields=["is_active"])
        check(False)
        permission.is_active = True
        permission.save(update_fields=["is_active"])
        self.realm.is_active = False
        self.realm.save(update_fields=["is_active"])
        check(False)

    def test_creation_templates_match_across_django_and_node(self):
        import json
        import subprocess
        from entity.role_templates import template_permissions
        root = Path(__file__).resolve().parents[4]
        code = "const {templatePermissions}=require('./reusables/models/roleTemplates');process.stdout.write(JSON.stringify(Object.fromEntries(['community','page','group','server','channel','voice','conference'].map(t=>[t,Object.fromEntries(['owner','admin','member'].map(c=>[c,templatePermissions(c,t)]))]))));"
        actual = json.loads(subprocess.check_output(["node", "--preserve-symlinks", "--preserve-symlinks-main", "-e", code], cwd=root / "server"))
        for kind, definitions in actual.items():
            for name, permissions in definitions.items():
                self.assertEqual(set(permissions), template_permissions(name, kind), (kind, name))
