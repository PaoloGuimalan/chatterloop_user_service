from django.test import TestCase

from entity.models import PermissionCatalogEntry
from entity.permissions import Permission


class PermissionCatalogSyncTests(TestCase):
    """
    entity/permissions.py's Permission class of string constants is kept
    alongside the database-backed PermissionCatalogEntry table for IDE
    typo-safety at call sites - but that means the two can drift: a
    Permission.X constant with no matching DB row (call sites would raise
    ValueError at runtime), or a DB row with no constant (harmless - just
    means no code references it yet, expected for admin-defined/future API
    scopes). This test only guards the direction that would actually break
    something: every constant must have a matching, active catalog row.
    """

    def test_every_permission_constant_has_an_active_catalog_row(self):
        active_codenames = set(
            PermissionCatalogEntry.objects.filter(is_active=True).values_list(
                "codename", flat=True
            )
        )
        missing = Permission.ALL - active_codenames
        self.assertEqual(
            missing,
            set(),
            f"Permission.* constants with no active PermissionCatalogEntry row: {missing}",
        )

    def test_catalog_scope_matches_permission_py_scope_sets(self):
        global_rows = set(
            PermissionCatalogEntry.objects.filter(scope="global").values_list(
                "codename", flat=True
            )
        )
        realm_rows = set(
            PermissionCatalogEntry.objects.filter(scope="realm").values_list(
                "codename", flat=True
            )
        )
        self.assertEqual(global_rows & Permission.GLOBAL_SCOPED, Permission.GLOBAL_SCOPED)
        self.assertEqual(realm_rows & Permission.REALM_SCOPED, Permission.REALM_SCOPED)
