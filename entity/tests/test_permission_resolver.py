from datetime import timedelta

from django_redis import get_redis_connection
from django.test import TestCase
from django.utils.timezone import now

from community.models import Member, Realm
from entity.models import Entity, EntityPermission
from entity.permissions import MemberRole, Permission, PermissionEffect
from entity.services.permission_catalog_cache import (
    CATALOG_CACHE_KEY,
    ROLE_MATRIX_CACHE_KEY,
)
from entity.services.permission_resolver import has_permission


def _clear_permission_cache():
    conn = get_redis_connection("default")
    conn.delete(CATALOG_CACHE_KEY, ROLE_MATRIX_CACHE_KEY)


def _make_entity(entity_type="user"):
    return Entity.objects.create(type=entity_type)


def _make_realm(created_by):
    realm_entity = _make_entity(entity_type="realm")
    return Realm.objects.create(
        entity=realm_entity,
        name="Test Realm",
        created_by=created_by,
        type="server",
    )


class HasPermissionTests(TestCase):
    def setUp(self):
        # The catalog/role-matrix cache lives in the real shared Redis
        # instance (not a per-test in-memory cache), so clear it around
        # every test - otherwise a stale read from a previous run (or a
        # write that outlives this test's DB transaction rollback) can
        # leak between tests or into real app traffic.
        _clear_permission_cache()
        self.addCleanup(_clear_permission_cache)

        self.entity = _make_entity()
        self.creator = _make_entity()
        self.realm = _make_realm(created_by=self.creator)

    # --- basic validation ---

    def test_none_entity_denied(self):
        self.assertFalse(has_permission(None, Permission.MESSAGES_SEND))

    def test_unknown_permission_raises(self):
        with self.assertRaises(ValueError):
            has_permission(self.entity, "not.a.real.permission")

    def test_global_permission_with_realm_raises(self):
        with self.assertRaises(ValueError):
            has_permission(self.entity, Permission.MESSAGES_SEND, realm=self.realm)

    def test_realm_permission_without_realm_raises(self):
        with self.assertRaises(ValueError):
            has_permission(self.entity, Permission.REALM_UPDATE)

    # --- realm-scoped role defaults ---

    def test_non_member_denied_realm_scoped(self):
        self.assertFalse(has_permission(self.entity, Permission.REALM_UPDATE, realm=self.realm))

    def test_owner_gets_realm_update(self):
        Member.objects.create(
            entity=self.entity, realm=self.realm, added_by=self.creator,
            role=MemberRole.OWNER, date_joined=now(),
        )
        self.assertTrue(has_permission(self.entity, Permission.REALM_UPDATE, realm=self.realm))

    def test_plain_member_denied_realm_update(self):
        Member.objects.create(
            entity=self.entity, realm=self.realm, added_by=self.creator,
            role=MemberRole.MEMBER, date_joined=now(),
        )
        self.assertFalse(has_permission(self.entity, Permission.REALM_UPDATE, realm=self.realm))

    def test_plain_member_gets_member_view(self):
        Member.objects.create(
            entity=self.entity, realm=self.realm, added_by=self.creator,
            role=MemberRole.MEMBER, date_joined=now(),
        )
        self.assertTrue(has_permission(self.entity, Permission.REALM_MEMBER_VIEW, realm=self.realm))

    def test_moderator_can_approve_requests_but_not_update_realm(self):
        Member.objects.create(
            entity=self.entity, realm=self.realm, added_by=self.creator,
            role=MemberRole.MODERATOR, date_joined=now(),
        )
        self.assertTrue(has_permission(self.entity, Permission.REALM_REQUEST_APPROVE, realm=self.realm))
        self.assertFalse(has_permission(self.entity, Permission.REALM_UPDATE, realm=self.realm))

    # --- global-scoped platform default ---

    def test_active_account_allowed_by_platform_default(self):
        # entity.users has no related Account in this test -> falls back to True
        self.assertTrue(has_permission(self.entity, Permission.MESSAGES_SEND))

    # --- explicit overrides beat defaults ---

    def test_explicit_deny_beats_platform_default(self):
        EntityPermission.objects.create(
            entity=self.entity, permission=Permission.MESSAGES_SEND,
            effect=PermissionEffect.DENY, realm=None,
        )
        self.assertFalse(has_permission(self.entity, Permission.MESSAGES_SEND))

    def test_explicit_grant_beats_role_default(self):
        Member.objects.create(
            entity=self.entity, realm=self.realm, added_by=self.creator,
            role=MemberRole.MEMBER, date_joined=now(),
        )
        EntityPermission.objects.create(
            entity=self.entity, permission=Permission.REALM_UPDATE,
            effect=PermissionEffect.GRANT, realm=self.realm,
        )
        self.assertTrue(has_permission(self.entity, Permission.REALM_UPDATE, realm=self.realm))

    def test_explicit_deny_beats_owner_role_default(self):
        Member.objects.create(
            entity=self.entity, realm=self.realm, added_by=self.creator,
            role=MemberRole.OWNER, date_joined=now(),
        )
        EntityPermission.objects.create(
            entity=self.entity, permission=Permission.REALM_UPDATE,
            effect=PermissionEffect.DENY, realm=self.realm,
        )
        self.assertFalse(has_permission(self.entity, Permission.REALM_UPDATE, realm=self.realm))

    # --- expiry ---

    def test_expired_deny_falls_through_to_default(self):
        EntityPermission.objects.create(
            entity=self.entity, permission=Permission.MESSAGES_SEND,
            effect=PermissionEffect.DENY, realm=None,
            expires_at=now() - timedelta(minutes=1),
        )
        self.assertTrue(has_permission(self.entity, Permission.MESSAGES_SEND))

    def test_unexpired_deny_still_blocks(self):
        EntityPermission.objects.create(
            entity=self.entity, permission=Permission.MESSAGES_SEND,
            effect=PermissionEffect.DENY, realm=None,
            expires_at=now() + timedelta(minutes=5),
        )
        self.assertFalse(has_permission(self.entity, Permission.MESSAGES_SEND))
