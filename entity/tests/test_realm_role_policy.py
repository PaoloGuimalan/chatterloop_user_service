from types import SimpleNamespace
import json
from unittest.mock import patch

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from rest_framework.test import APIRequestFactory, force_authenticate

from community import models as community_models
from community.models import Member, Realm
from entity.models import Entity, EntityPermission, PermissionCatalogEntry, Role
from entity.role_views import RoleDetailView, RolesView
from entity.services.permission_resolver import has_permission


class RealmRolePolicyFixture:
    def setUp(self):
        self.cache = {}
        for name, effect in (("_redis_get", self.cache.get), ("_redis_set", self.cache.__setitem__),
                             ("_redis_delete", lambda key: self.cache.pop(key, None))):
            self.enterContext(patch(f"entity.services.permission_catalog_cache.{name}", side_effect=effect))
        self.actor = Entity.objects.create(type="user")
        self.catalog = {}
        for code in ("realm.member.view", "realm.member.role.update", "realm.roles.manage", "realm.permissions.manage",
                     "realm.post.submit", "realm.post.publish", "realm.post.review", "realm.ownership.transfer", "realm.identity.act"):
            self.catalog[code] = PermissionCatalogEntry.objects.create(codename=code, scope="realm")
        self.realm = self.make_realm("A")
        self.other = self.make_realm("B")
        self.roles = {r.code: r for r in self.realm.owned_roles.all()}
        self.roles["moderator"] = Role.objects.create(name="Moderator", code="moderator", is_default=True, realm=self.realm)
        self.roles["admin"].permissions.set(self.catalog.values())
        self.roles["member"].permissions.set([self.catalog["realm.member.view"], self.catalog["realm.post.submit"], self.catalog["realm.post.publish"]])
        Member.objects.create(entity=self.actor, realm=self.realm, added_by=self.actor, role=self.roles["owner"])

    def make_realm(self, name):
        return Realm.objects.create(entity=Entity.objects.create(type="realm"), created_by=self.actor, name=name, type="community")

    def request(self, method="get", body=None, params=""):
        request = getattr(APIRequestFactory(), method)("/api/entity/roles" + params, data=body or {}, format="json")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        request.entity = self.actor
        return RolesView.as_view()(request)


class RealmRolePolicyTests(RealmRolePolicyFixture, TestCase):
    def test_legacy_community_admin_label_is_preserved_without_making_it_an_acting_identity(self):
        from user.views import UserAuthentication
        from entity.services.acting import can_act_as_entity
        request = APIRequestFactory().get('/profile', {'type': 'manage'})
        request.entity = self.actor
        account = SimpleNamespace(is_authenticated=True, entity_id=self.actor.pk)
        force_authenticate(request, user=account)
        with patch('user.views.save_profile_visit'), patch('user.views.RabbitMQClient.publish_on_commit'):
            response = UserAuthentication.as_view()(request, username=self.realm.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['data']['is_admin'])
        self.assertFalse(can_act_as_entity(account, self.realm.entity))
    def test_permission_writes_refresh_existing_realm_members_only_after_commit(self):
        from community.permission_views import RealmPermissionsView
        request = APIRequestFactory().put('/permissions', {'roles': [
            {'role_id': self.roles['member'].pk, 'permissions': ['realm.post.submit']}]}, format='json')
        request.entity = self.actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch('community.views.InviteView._notify_members_changed') as publish:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(RealmPermissionsView.as_view()(request, realm_id=self.realm.pk).status_code, 200)
                publish.assert_not_called()
            publish.assert_called_once()
    def test_media_and_archive_delegates_receive_their_narrow_management_entry(self):
        from community.role_policy import realm_capabilities
        role = Role.objects.create(realm=self.realm, name='Media team')
        Member.objects.filter(realm=self.realm, entity=self.actor).update(role=role)
        for code, key in (('realm.media.update', 'can_update_media'), ('realm.post.archive', 'can_archive_community_posts'),
                          ('realm.invite.create', 'can_invite_members'), ('realm.member.remove', 'can_remove_members')):
            entry = PermissionCatalogEntry.objects.create(codename=code, scope='realm')
            role.permissions.set([entry])
            caps = realm_capabilities(self.actor, self.realm)
            self.assertTrue(caps.get(key))
            self.assertTrue(caps['can_manage'])
            self.assertFalse(caps['can_manage_permissions'])
    def test_permission_editor_can_change_nonadministrative_grants_without_role_naming_power(self):
        from community.permission_views import RealmPermissionsView
        editor = Role.objects.create(realm=self.realm, name='Permission editor')
        editor.permissions.add(self.catalog['realm.permissions.manage'], self.catalog['realm.post.submit'])
        target = Role.objects.create(realm=self.realm, name='Contributor')
        Member.objects.filter(realm=self.realm, entity=self.actor).update(role=editor)
        request = APIRequestFactory().put('/permissions', {'roles': [
            {'role_id': target.pk, 'permissions': ['realm.post.submit']}]}, format='json')
        request.entity = self.actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        self.assertEqual(RealmPermissionsView.as_view()(request, realm_id=self.realm.pk).status_code, 200)
        self.assertEqual(set(target.permissions.values_list('codename', flat=True)), {'realm.post.submit'})
        rename = APIRequestFactory().put('/roles', {'realm_id': self.realm.pk, 'name': 'Renamed'}, format='json')
        rename.entity = self.actor
        force_authenticate(rename, user=SimpleNamespace(is_authenticated=True))
        self.assertEqual(RoleDetailView.as_view()(rename, role_id=target.pk).status_code, 403)
    def test_catalog_upgrade_preserves_explicit_inviter_delivery(self):
        import importlib
        from django.apps import apps
        from django.utils.timezone import now
        from datetime import timedelta

        create = PermissionCatalogEntry.objects.create(codename="realm.invite.create", scope="realm")
        original = EntityPermission.objects.create(entity=self.actor, realm=self.realm, permission=create,
                                                   effect="grant", expires_at=now() + timedelta(days=1))
        migration = importlib.import_module("entity.migrations.0027_community_permission_catalog")
        migration.seed_policy(apps, SimpleNamespace(connection=connection))
        copied = EntityPermission.objects.get(entity=self.actor, realm=self.realm, permission__codename="realm.invite.publish")
        self.assertEqual(copied.effect, "grant")
        self.assertEqual(copied.expires_at, original.expires_at)
        migration.seed_policy(apps, SimpleNamespace(connection=connection))
        self.assertEqual(EntityPermission.objects.filter(pk=copied.pk).count(), 1)

    def test_profile_contract_exposes_capabilities_without_changing_legacy_tier(self):
        from community.serializers import RealmSerializer
        self.realm.my_role = "owner"
        self.realm.my_role_id = self.roles["owner"].pk
        result = RealmSerializer(self.realm, context={"request": SimpleNamespace(entity=self.actor)}).data
        self.assertEqual(result["my_role"], "owner")
        self.assertEqual(result["my_role_name"], "Owner")
        self.assertEqual(result["my_role_code"], "owner")
        self.assertTrue(result["capabilities"]["can_manage_permissions"])
        self.assertTrue(result["features"]["realm_role_configuration"])

    def test_request_context_never_overrides_fresh_authorization(self):
        Member.objects.filter(entity=self.actor, realm=self.realm).update(role=self.roles["member"])
        context = {}
        self.assertTrue(has_permission(self.actor, "realm.post.publish", self.realm, context=context))
        EntityPermission.objects.create(entity=self.actor, realm=self.realm,
                                        permission=self.catalog["realm.post.publish"], effect="deny")
        self.assertFalse(has_permission(self.actor, "realm.post.publish", self.realm, fresh=True, context=context))

    def test_unscoped_role_list_never_leaks_private_role_definitions(self):
        private = Role.objects.create(name="Private reviewer", realm=self.other)
        response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(private.pk, {r["id"] for r in response.data["result"]})

    def test_scoped_list_combines_shared_and_owned_roles_only(self):
        own = Role.objects.create(name="A reviewer", realm=self.realm)
        foreign = Role.objects.create(name="B reviewer", realm=self.other)
        response = self.request(params=f"?realm_id={self.realm.pk}")
        ids = {r["id"] for r in response.data["result"]}
        self.assertIn(own.pk, ids)
        self.assertIn(self.roles["member"].pk, ids)
        self.assertNotIn(foreign.pk, ids)

    def test_nonmember_cannot_query_another_realms_custom_roles(self):
        response = self.request(params=f"?realm_id={self.other.pk}")
        self.assertIn(response.status_code, (403, 404))

    def test_owner_can_create_custom_role_but_cannot_input_identity_fields(self):
        response = self.request("post", {"realm_id": self.realm.pk, "name": "Post reviewer", "permissions": ["realm.post.review"]})
        self.assertEqual(response.status_code, 201)
        role = Role.objects.get(pk=response.data["result"]["id"])
        self.assertEqual(role.realm_id, self.realm.pk)
        self.assertEqual(set(role.permissions.values_list("codename", flat=True)), {"realm.post.review"})
        for field, value in (("code", "owner"), ("is_default", True), ("base_role", self.roles["owner"].pk)):
            with self.subTest(field=field):
                denied = self.request("post", {"realm_id": self.realm.pk, "name": "Other", field: value})
                self.assertEqual(denied.status_code, 400)

    def test_owner_can_configure_roles_without_environment_opt_in(self):
        response = self.request("post", {"realm_id": self.realm.pk, "name": "Reviewer"})
        self.assertEqual(response.status_code, 201)
        role = Role.objects.get(pk=response.data["result"]["id"])
        self.assertEqual(role.realm_id, self.realm.pk)

    def test_admin_cannot_create_role_with_delegation_authority(self):
        member = Member.objects.get(entity=self.actor, realm=self.realm)
        member.role = self.roles["admin"]
        member.save()
        response = self.request("post", {"realm_id": self.realm.pk, "name": "Another admin", "permissions": ["realm.roles.manage"]})
        self.assertEqual(response.status_code, 403)

    def test_role_grants_are_local_and_explicit_entity_deny_still_wins(self):
        person = Entity.objects.create(type="user")
        for realm in (self.realm, self.other):
            Member.objects.create(entity=person, realm=realm, added_by=self.actor,
                                  role=realm.owned_roles.get(code="member"))
        self.roles["member"].permissions.remove(self.catalog["realm.post.publish"])
        self.assertFalse(has_permission(person, "realm.post.publish", self.realm))
        self.assertTrue(has_permission(person, "realm.post.publish", self.other))
        self.roles["member"].permissions.add(self.catalog["realm.post.review"])
        EntityPermission.objects.create(entity=person, realm=self.realm,
            permission=self.catalog["realm.post.review"], effect="deny")
        self.assertFalse(has_permission(person, "realm.post.review", self.realm))

    def test_global_capabilities_cannot_be_put_in_a_custom_role(self):
        PermissionCatalogEntry.objects.create(codename="posts.create", scope="global")
        response = self.request("post", {"realm_id": self.realm.pk, "name": "Bypass", "permissions": ["posts.create"]})
        self.assertEqual(response.status_code, 400)

    def detail(self, role, method, body):
        request = getattr(APIRequestFactory(), method)(f"/api/entity/roles/{role.pk}", body, format="json")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        request.entity = self.actor
        return RoleDetailView.as_view()(request, role_id=role.pk)

    def test_local_default_roles_are_editable_but_foreign_roles_are_not(self):
        shared = self.detail(self.roles["member"], "put", {"realm_id": self.realm.pk, "name": "Changed"})
        self.assertEqual(shared.status_code, 200)
        foreign = Role.objects.create(name="Foreign", realm=self.other)
        response = self.detail(foreign, "put", {"realm_id": self.realm.pk, "name": "Changed"})
        self.assertEqual(response.status_code, 404)

    def test_omitted_permissions_copy_effective_member_preset_once(self):
        response = self.request("post", {"realm_id": self.realm.pk, "name": "New member role"})
        self.assertEqual(response.status_code, 201)
        role = Role.objects.get(pk=response.data["result"]["id"])
        expected = {"realm.member.view", "realm.post.submit", "realm.post.publish"}
        self.assertEqual(set(role.permissions.values_list("codename", flat=True)), expected)
        self.roles["member"].permissions.clear()
        self.assertEqual(set(role.permissions.values_list("codename", flat=True)), expected)

    def test_assigned_administrative_role_cannot_be_edited_by_admin(self):
        role = Role.objects.create(name="Delegated admin", realm=self.realm)
        role.permissions.add(self.catalog["realm.roles.manage"])
        holder = Entity.objects.create(type="user")
        Member.objects.create(entity=holder, realm=self.realm, added_by=self.actor, role=role)
        member = Member.objects.get(entity=self.actor, realm=self.realm)
        member.role = self.roles["admin"]
        member.save()
        response = self.detail(role, "put", {"realm_id": self.realm.pk, "name": "Bypass"})
        self.assertEqual(response.status_code, 403)

    def test_resolved_invites_still_protect_role_deletion(self):
        role = Role.objects.create(name="Reviewer", realm=self.realm)
        community_models.Invite.objects.create(realm=self.realm, created_by=self.actor, role=role, status="accepted")
        response = self.detail(role, "delete", {"realm_id": self.realm.pk})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Role.objects.filter(pk=role.pk).exists())

    def test_assigned_custom_role_deletion_preserves_members_and_permissions(self):
        role = Role.objects.create(name='Review team', realm=self.realm)
        role.permissions.add(self.catalog['realm.post.review'])
        person = Entity.objects.create(type='user')
        member = Member.objects.create(entity=person, realm=self.realm, added_by=self.actor, role=role)
        response = self.detail(role, 'delete', {'realm_id': self.realm.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn('members or invitations', str(response.data))
        member.refresh_from_db()
        self.assertEqual(member.role_id, role.pk)
        self.assertTrue(Role.objects.filter(pk=role.pk).exists())
        self.assertTrue(has_permission(person, 'realm.post.review', self.realm, fresh=True))

    def test_reassigning_members_allows_deletion_of_an_unused_custom_role(self):
        role = Role.objects.create(name='Old team', realm=self.realm)
        person = Entity.objects.create(type='user')
        member = Member.objects.create(entity=person, realm=self.realm, added_by=self.actor, role=role)
        member.role = self.roles['member']
        member.save(update_fields=['role'])
        response = self.detail(role, 'delete', {'realm_id': self.realm.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Role.objects.filter(pk=role.pk).exists())
        member.refresh_from_db()
        self.assertEqual(member.role_id, self.roles['member'].pk)
        self.assertTrue(has_permission(person, 'realm.post.submit', self.realm, fresh=True))

    def test_default_admin_and_member_roles_cannot_be_deleted_even_when_unassigned(self):
        for code in ('admin', 'member'):
            with self.subTest(role=code):
                role = self.roles[code]
                self.assertFalse(role.memberships.exists())
                response = self.detail(role, 'delete', {'realm_id': self.realm.pk})
                self.assertEqual(response.status_code, 400)
                self.assertTrue(Role.objects.filter(pk=role.pk).exists())

    def test_commit_invalidation_removes_a_late_stale_role_cache_write(self):
        person = Entity.objects.create(type="user")
        role = self.roles["member"]
        Member.objects.create(entity=person, realm=self.realm, added_by=self.actor, role=role)
        with self.captureOnCommitCallbacks(execute=True):
            role.permissions.remove(self.catalog["realm.post.publish"])
            # Another service can populate its pre-commit view after the first deletion.
            self.cache[f"permcache:role_matrix:v3:{self.realm.pk}"] = json.dumps({str(role.pk): ["realm.post.publish"]})
        self.assertFalse(has_permission(person, "realm.post.publish", self.realm))

    def test_post_reviewer_has_manage_entry_without_member_management(self):
        from community import role_policy

        helper = getattr(role_policy, "realm_capabilities", None)
        self.assertTrue(callable(helper), "Realm capabilities must be computed by the server")
        role = Role.objects.create(name="Editorial reviewer", realm=self.realm)
        role.permissions.add(self.catalog["realm.post.review"])
        member = Member.objects.get(entity=self.actor, realm=self.realm)
        member.role = role
        member.save()
        caps = helper(self.actor, self.realm)
        self.assertTrue(caps["can_manage"])
        self.assertTrue(caps["can_review_posts"])
        self.assertFalse(caps["can_assign_roles"])
        self.assertFalse(caps["can_manage_permissions"])

    def test_capability_summary_does_not_repeat_queries_per_permission(self):
        from community.role_policy import realm_capabilities

        self.cache.clear()
        with CaptureQueriesContext(connection) as queries:
            result = realm_capabilities(self.actor, self.realm)
        self.assertTrue(result["can_manage_permissions"])
        self.assertLessEqual(len(queries), 8, "Profile capability summaries must batch shared authorization reads")

    def test_disabled_role_configuration_capability_denies_instead_of_crashing(self):
        self.catalog["realm.roles.manage"].is_active = False
        self.catalog["realm.roles.manage"].save(update_fields=["is_active"])
        response = self.request("post", {"realm_id":self.realm.pk,"name":"Reviewer"})
        self.assertEqual(response.status_code,403)
