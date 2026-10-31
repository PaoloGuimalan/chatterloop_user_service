from unittest.mock import patch
from types import SimpleNamespace

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from entity.models import Role, PermissionCatalogEntry
from entity.role_views import RolesView


class RolesViewTests(TestCase):
    def test_authenticated_list_keeps_duplicate_role_ids_and_permissions_distinct(self):
        with patch("entity.services.permission_catalog_cache._redis_delete"):
            first = Role.objects.create(name="admin")
            second = Role.objects.create(name="admin")
            first.permissions.add(PermissionCatalogEntry.objects.get(codename="realm.update"))
        request = APIRequestFactory().get("/api/entity/roles")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        response = RolesView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.data["result"]}
        self.assertEqual(rows[first.pk]["name"], "admin")
        self.assertEqual(rows[second.pk]["name"], "admin")
        self.assertEqual(rows[first.pk]["permissions"], ["realm.update"])
        self.assertEqual(rows[second.pk]["permissions"], [])
        self.assertFalse(rows[first.pk]["is_default"])

    def test_unauthenticated_role_listing_is_rejected(self):
        response = RolesView.as_view()(APIRequestFactory().get("/api/entity/roles"))
        self.assertIn(response.status_code, (401, 403))


class RoleReferenceMigrationTests(TransactionTestCase):
    before = [("entity", "0021_messages_encryption_purpose"),
              ("community", "0010_community_membership_capability")]
    after = [("entity", "0024_catalog_entity_types"), ("community", "0011_role_references")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_member_and_invite_names_migrate_to_exact_ids_and_reverse(self):
        old = self.migrate(self.before)
        self.addCleanup(self.migrate, self.after)
        entity = old.get_model("entity", "Entity")
        owner = entity.objects.create(type="user")
        realm = old.get_model("community", "Realm").objects.create(
            id="role-reference-realm", realm_id="role-reference-realm", name="Roles",
            entity=entity.objects.create(type="realm"), created_by=owner, type="community",
        )
        member_model = old.get_model("community", "Member")
        first = member_model.objects.create(entity=owner, realm=realm, added_by=owner, role="owner")
        second = member_model.objects.create(entity=entity.objects.create(type="user"), realm=realm,
                                              added_by=owner, role="legacy-special-role")
        invite_model = old.get_model("community", "Invite")
        manage = invite_model.objects.create(realm=realm, created_by=owner, purpose="manage", role="admin")
        join = invite_model.objects.create(realm=realm, created_by=owner, purpose="join", role=None)
        member_fields = ("member_id", "entity_id", "realm_id", "added_by_id", "date_joined", "nickname")
        expected_members = list(member_model.objects.order_by("member_id").values_list(*member_fields))
        current = self.migrate(self.after)
        members = current.get_model("community", "Member")
        invites = current.get_model("community", "Invite")
        roles = current.get_model("entity", "Role")
        self.assertEqual(list(members.objects.order_by("member_id").values_list(*member_fields)), expected_members)
        self.assertEqual(members.objects.get(pk=first.pk).role_id, roles.objects.get(name="owner", is_default=True).pk)
        self.assertEqual(members.objects.get(pk=second.pk).role.name, "legacy-special-role")
        self.assertEqual(invites.objects.get(pk=manage.pk).role_id, roles.objects.get(name="admin", is_default=True).pk)
        self.assertIsNone(invites.objects.get(pk=join.pk).role_id)
        old = self.migrate(self.before)
        self.assertEqual(old.get_model("community", "Member").objects.get(pk=first.pk).role, "owner")
        self.assertEqual(old.get_model("community", "Member").objects.get(pk=second.pk).role, "legacy-special-role")
        self.assertEqual(old.get_model("community", "Invite").objects.get(pk=manage.pk).role, "admin")
        self.assertIsNone(old.get_model("community", "Invite").objects.get(pk=join.pk).role)
