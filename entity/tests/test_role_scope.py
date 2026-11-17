from unittest.mock import patch
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from community.models import Member, Realm
from entity.models import Entity, Role
from entity.roles import resolve_role


class RoleScopeTests(TestCase):
    def setUp(self):
        cache = patch("entity.services.permission_catalog_cache._redis_delete")
        cache.start()
        self.addCleanup(cache.stop)
        self.owner = Entity.objects.create(type="user")
        self.realm = self.make_realm("Community A")
        self.other = self.make_realm("Community B")

    def make_realm(self, name):
        return Realm.objects.create(
            name=name, entity=Entity.objects.create(type="realm"),
            created_by=self.owner, type="community",
        )

    def require_scope(self):
        self.assertTrue(
            {"code", "realm"}.issubset({f.name for f in Role._meta.fields}),
            "Roles need stable identity and explicit realm ownership",
        )

    def test_custom_role_identity_is_generated_and_locally_distinct(self):
        self.require_scope()
        first = Role.objects.create(name="Post Reviewer", realm=self.realm)
        same = Role.objects.create(name="Post Reviewer", realm=self.realm)
        elsewhere = Role.objects.create(name="Post Reviewer", realm=self.other)
        self.assertEqual(first.code, "post-reviewer")
        self.assertNotEqual(first.code, same.code)
        self.assertEqual(first.code, elsewhere.code)
        self.assertEqual(first.realm_id, self.realm.pk)
        self.assertLessEqual(len(same.code), 255)

    def test_shared_default_code_and_custom_display_name_do_not_confer_tier(self):
        self.require_scope()
        from entity.roles import role_tier

        shared = self.realm.owned_roles.get(code="admin")
        custom = Role.objects.create(name="admin", realm=self.realm)
        self.assertEqual(shared.realm_id, self.realm.pk)
        self.assertEqual(shared.code, "admin")
        self.assertEqual(role_tier(shared), "admin")
        self.assertEqual(role_tier(custom), "member")

    def test_rename_preserves_identity_and_existing_membership(self):
        self.require_scope()
        role = Role.objects.create(name="Reviewer", realm=self.realm)
        code = role.code
        member = Member.objects.create(entity=self.owner, realm=self.realm, added_by=self.owner, role=role)
        role.name = "Editorial Team"
        role.save()
        member.refresh_from_db()
        self.assertEqual(role.code, code)
        self.assertEqual(member.role_id, role.pk)

    def test_role_code_and_realm_are_immutable(self):
        self.require_scope()
        role = Role.objects.create(name="Reviewer", realm=self.realm)
        for field, value in (("code", "owner"), ("realm_id", self.other.pk)):
            with self.subTest(field=field):
                role.refresh_from_db()
                setattr(role, field, value)
                with self.assertRaises(ValidationError):
                    role.save()

    def test_exact_id_lookup_rejects_foreign_or_missing_scope(self):
        self.require_scope()
        role = Role.objects.create(name="Reviewer", realm=self.realm)
        self.assertEqual(resolve_role(role_id=role.pk, realm=self.realm), role)
        for scope in (self.other, None):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                resolve_role(role_id=role.pk, realm=scope)

    def test_default_roles_belong_to_their_realm(self):
        role = self.realm.owned_roles.get(code="member")
        self.assertEqual(resolve_role("member", realm=self.realm), role)
        with self.assertRaises(ValueError):
            resolve_role(role_id=role.pk, realm=self.other)

    def test_realm_delete_cannot_turn_custom_roles_into_shared_roles(self):
        self.require_scope()
        role = Role.objects.create(name="Reviewer", realm=self.realm)
        realm_id = self.realm.pk
        self.realm.delete()
        self.assertFalse(Role.objects.filter(pk=role.pk).exists())
        self.assertFalse(Role.objects.filter(realm_id=realm_id).exists())

    def test_long_and_non_latin_names_still_generate_bounded_unique_codes(self):
        self.require_scope()
        long = Role.objects.create(name="x" * 150, realm=self.realm)
        unicode = Role.objects.create(name="审核员", realm=self.realm)
        self.assertEqual(unicode.code, "role")
        self.assertLessEqual(len(long.code), 255)

    def test_profile_role_annotation_retains_renamed_default_admin_tier(self):
        from community.annotations import my_role_annotation
        role = self.realm.owned_roles.get(code="admin")
        role.name = "Renamed operations"
        role.save()
        Member.objects.create(entity=self.owner, realm=self.realm, added_by=self.owner, role=role)
        annotated = Realm.objects.annotate(my_role=my_role_annotation(self.owner)).get(pk=self.realm.pk)
        self.assertEqual(annotated.my_role, "admin")
