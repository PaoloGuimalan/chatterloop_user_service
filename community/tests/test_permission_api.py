from types import SimpleNamespace
from importlib import import_module, util

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from entity.models import Role
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture


class PermissionApiTests(RealmRolePolicyFixture, TestCase):
    def rules(self, body=None):
        self.assertIsNotNone(util.find_spec("community.permission_views"), "Realm permissions endpoint is not implemented")
        permission_views = import_module("community.permission_views")
        request = APIRequestFactory().put("/api/community/realms/id/permissions", body or {}, format="json")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        request.entity = self.actor
        return permission_views.RealmPermissionsView.as_view()(request, realm_id=self.realm.pk)

    def test_invalid_foreign_role_does_not_partially_change_local_grants(self):
        before = set(self.roles["member"].permissions.values_list("pk", flat=True))
        foreign = Role.objects.create(name="Other", realm=self.other)
        response = self.rules({"roles": [
            {"role_id": self.roles["member"].pk, "permissions": []},
            {"role_id": foreign.pk, "permissions": ["realm.post.publish"]}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(self.roles["member"].permissions.values_list("pk", flat=True)), before)

    def test_owner_permissions_are_intrinsic_and_cannot_be_replaced(self):
        response = self.rules({"roles": [{"role_id": self.roles["owner"].pk, "permissions": []}]})
        self.assertEqual(response.status_code, 403)

    def test_permissions_edit_the_local_role_and_leave_other_realms_unchanged(self):
        response = self.rules({"roles": [{"role_id": self.roles["member"].pk, "permissions": ["realm.post.submit"]}]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.roles["member"].permissions.filter(codename="realm.post.publish").exists())
        self.assertTrue(self.other.owned_roles.get(code="member").permissions.filter(codename="realm.post.publish").exists())

    def test_owner_can_edit_grants_without_environment_opt_in(self):
        response = self.rules({"roles": [{"role_id": self.roles["member"].pk,
                                        "permissions": ["realm.post.submit"]}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(self.roles["member"].permissions.values_list("codename", flat=True)),
                         {"realm.post.submit"})

    def test_member_cannot_edit_grants_without_permission(self):
        from community.models import Member
        Member.objects.filter(entity=self.actor, realm=self.realm).update(role=self.roles["member"])
        before = set(self.roles["member"].permissions.values_list("codename", flat=True))
        self.assertEqual(self.rules({"roles": [{"role_id": self.roles["member"].pk,
                                              "permissions": []}]}).status_code, 403)
        self.assertEqual(set(self.roles["member"].permissions.values_list("codename", flat=True)), before)

    def test_admin_cannot_change_rules_for_a_custom_administrative_role(self):
        from community.models import Member
        from entity.models import Entity, Role

        role = Role.objects.create(name="Operations", realm=self.realm)
        role.permissions.add(self.catalog["realm.roles.manage"])
        holder = Entity.objects.create(type="user")
        Member.objects.create(entity=holder, realm=self.realm, added_by=self.actor, role=role)
        member = Member.objects.get(entity=self.actor, realm=self.realm)
        member.role = self.roles["admin"]
        member.save()
        response = self.rules({"roles": [{"role_id": role.pk, "permissions": ["realm.post.submit"]}]})
        self.assertEqual(response.status_code, 403)
