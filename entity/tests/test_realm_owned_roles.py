import json
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from community.models import Member, Realm
from entity.models import Entity, EntityPermission, PermissionCatalogEntry, Role
from entity.roles import get_default_role
from entity.services.permission_resolver import has_permission


class RealmOwnedRoleTests(TestCase):
    def setUp(self):
        self.cache = {}
        for name, effect in (("_redis_get", self.cache.get), ("_redis_set", self.cache.__setitem__),
                             ("_redis_delete", lambda key: self.cache.pop(key, None))):
            self.enterContext(patch(f"entity.services.permission_catalog_cache.{name}", side_effect=effect))
        self.actor = Entity.objects.create(type="user")
        self.catalog = {}
        for code in ("realm.update", "realm.member.view", "realm.member.role.update", "realm.roles.manage",
                     "realm.permissions.manage", "realm.post.submit", "realm.post.publish", "realm.invite.create",
                     "realm.invite.publish", "realm.notifications.view"):
            self.catalog[code] = PermissionCatalogEntry.objects.create(codename=code, scope="realm")
        self.catalog["posts.create"] = PermissionCatalogEntry.objects.create(codename="posts.create", scope="global")
        self.realm = self.make_realm("A")
        self.other = self.make_realm("B")

    def make_realm(self, name, kind="community"):
        return Realm.objects.create(entity=Entity.objects.create(type="realm"), created_by=self.actor, name=name, type=kind)

    def default(self, code, realm=None):
        return get_default_role(code, realm=realm or self.realm)

    def test_new_realms_get_owned_defaults_with_codes_reused_between_realms(self):
        for realm in (self.realm, self.other, self.make_realm("Server", "server"), self.make_realm("Page", "page")):
            self.assertEqual(set(realm.owned_roles.values_list("code", flat=True)), {"owner", "admin", "member"})
        self.assertNotEqual(self.default("member").pk, self.default("member", self.other).pk)
        self.assertFalse(Role.objects.filter(realm__isnull=True).exists())
        self.assertNotIn("base_role", {field.name for field in Role._meta.fields})

    def test_role_codes_are_unique_locally_without_realm_id_suffix(self):
        first = Role.objects.create(realm=self.realm, name="Event helpers")
        second = Role.objects.create(realm=self.other, name="Event helpers")
        self.assertEqual((first.code, second.code), ("event-helpers", "event-helpers"))
        duplicate = Role.objects.create(realm=self.realm, name="Event helpers")
        self.assertNotEqual(first.code, duplicate.code)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Role.objects.bulk_create([Role(realm=self.realm, name="Other", code=first.code)])
        with self.assertRaises(ValidationError):
            Role.objects.create(name="No global roles")

    def test_owner_is_immutable_and_intrinsically_allowed_realm_permissions(self):
        owner = self.default("owner")
        Member.objects.create(entity=self.actor, realm=self.realm, added_by=self.actor, role=owner)
        EntityPermission.objects.create(entity=self.actor, realm=self.realm, permission=self.catalog["realm.roles.manage"], effect="deny")
        self.assertTrue(has_permission(self.actor, "realm.roles.manage", self.realm, fresh=True))
        EntityPermission.objects.create(entity=self.actor, permission=self.catalog["posts.create"], effect="deny")
        self.assertFalse(has_permission(self.actor, "posts.create", fresh=True))
        owner.name = "Changed"
        with self.assertRaises(ValidationError):
            owner.save()
        with self.assertRaises(ValidationError):
            owner.delete()

    def test_member_permissions_are_direct_local_grants_and_templates_do_not_reset_them(self):
        from entity.roles import ensure_default_roles
        member = self.default("member")
        member.permissions.remove(self.catalog["realm.post.publish"])
        member.name = "Participants"
        member.save()
        ensure_default_roles(self.realm)
        member.refresh_from_db()
        self.assertEqual(member.name, "Participants")
        self.assertFalse(member.permissions.filter(codename="realm.post.publish").exists())
        self.assertTrue(self.default("member", self.other).permissions.filter(codename="realm.post.publish").exists())

    def test_owner_permission_relation_is_not_editable(self):
        with self.assertRaises(ValidationError), transaction.atomic():
            self.default("owner").permissions.add(self.catalog["realm.roles.manage"])

    def test_page_self_annotation_resolves_that_pages_owned_owner_role(self):
        from community.annotations import my_role_id_annotation
        page = self.make_realm("Page", "page")
        result = Realm.objects.filter(pk=page.pk).annotate(my_role_id=my_role_id_annotation(page.entity)).get()
        self.assertEqual(result.my_role_id, self.default("owner", page).pk)

    def test_explicit_realm_exceptions_still_work_without_a_role(self):
        visitor = Entity.objects.create(type="user")
        EntityPermission.objects.create(entity=visitor, realm=self.realm,
            permission=self.catalog["realm.member.view"], effect="grant")
        self.assertTrue(has_permission(visitor, "realm.member.view", self.realm, fresh=True))

    def test_role_grant_cache_is_scoped_and_updates_only_its_own_realm(self):
        from entity.services.permission_catalog_cache import get_role_matrix
        own = self.default("member")
        other = self.default("member", self.other)
        first = get_role_matrix(self.realm.pk)
        self.assertIn(str(own.pk), first)
        self.assertNotIn(str(other.pk), first)
        get_role_matrix(self.other.pk)
        before = dict(self.cache)
        own.permissions.remove(self.catalog["realm.post.publish"])
        self.assertNotIn("realm.post.publish", get_role_matrix(self.realm.pk)[str(own.pk)])
        self.assertEqual(get_role_matrix(self.other.pk)[str(other.pk)], json.loads(
            before[f"permcache:role_matrix:v3:{self.other.pk}"])[str(other.pk)])

    def test_acting_as_realm_requires_its_identity_permission_not_a_display_name(self):
        from types import SimpleNamespace
        from entity.services.acting import can_act_as_entity, acting_memberships
        permission = PermissionCatalogEntry.objects.create(codename="realm.identity.act", scope="realm")
        page = self.make_realm("Page", "page")
        role = Role.objects.create(name="admin", realm=page)
        Member.objects.create(entity=self.actor, realm=page, added_by=self.actor, role=role)
        account = SimpleNamespace(entity_id=self.actor.pk)
        self.assertFalse(can_act_as_entity(account, page.entity))
        role.permissions.add(permission)
        self.assertTrue(can_act_as_entity(account, page.entity))
        self.assertEqual(list(acting_memberships(self.actor).values_list("realm_id", flat=True)), [page.pk])
        EntityPermission.objects.create(entity=self.actor, realm=page, permission=permission, effect="deny")
        self.assertFalse(can_act_as_entity(account, page.entity))
        Member.objects.filter(entity=self.actor, realm=page).update(role=self.default("owner", page))
        self.assertTrue(can_act_as_entity(account, page.entity))

    def test_revoked_identity_authority_blocks_existing_tokens_but_keeps_switch_back(self):
        from types import SimpleNamespace
        from user.backends import AutheticationBackend
        from django.core.exceptions import PermissionDenied
        permission = PermissionCatalogEntry.objects.create(codename="realm.identity.act", scope="realm")
        page = self.make_realm("Page", "page")
        member_role = self.default("member", page)
        Member.objects.create(entity=self.actor, realm=page, added_by=self.actor, role=member_role)
        account = SimpleNamespace(entity_id=self.actor.pk)
        backend = AutheticationBackend()
        request = SimpleNamespace(resolver_match=SimpleNamespace(view_name="api-user:user-management"))
        with self.assertRaisesRegex(PermissionDenied, "REALM_ACCESS_REVOKED: Your access to Page was revoked"):
            backend._check_acting_authority(request, account, page.entity)
        member_role.permissions.add(permission)
        backend._check_acting_authority(request, account, page.entity)
        member_role.permissions.remove(permission)
        with self.assertRaisesRegex(PermissionDenied, "REALM_ACCESS_REVOKED: Your access to Page was revoked"):
            backend._check_acting_authority(request, account, page.entity)
        request.resolver_match.view_name = "api-user:entity-switch-back"
        backend._check_acting_authority(request, account, page.entity)
        backend._check_acting_authority(request, account, self.actor)

    def test_group_and_server_invitation_permissions_can_be_revoked(self):
        from community.invites import assert_can_invite
        from community.invite_rules import InviteError
        for kind in ("group", "server"):
            realm = self.make_realm(kind, kind)
            role = self.default("member", realm)
            Member.objects.create(entity=self.actor, realm=realm, added_by=self.actor, role=role)
            assert_can_invite(self.actor, realm, "join", None, fresh=True)
            role.permissions.remove(self.catalog["realm.invite.create"])
            with self.assertRaises(InviteError):
                assert_can_invite(self.actor, realm, "join", None, fresh=True)
