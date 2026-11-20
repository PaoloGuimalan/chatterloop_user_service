from django.db import models
from django.test import SimpleTestCase, TestCase

from entity.models import PermissionCatalogEntry


class RealmTypeSchemaTests(SimpleTestCase):
    def test_permission_types_are_a_relation_to_the_community_type_model(self):
        from community.models import Type
        field = PermissionCatalogEntry._meta.get_field('realm_types')
        self.assertIsInstance(field, models.ManyToManyField)
        self.assertIs(field.remote_field.model, Type)
        self.assertEqual(Type._meta.db_table, 'community_type')

    def test_permission_type_join_is_managed_automatically_by_django(self):
        through = PermissionCatalogEntry._meta.get_field('realm_types').remote_field.through
        self.assertIs(through._meta.auto_created, PermissionCatalogEntry)
        self.assertEqual(through._meta.db_table, 'entity_permission_catalog_realm_types')
        self.assertEqual({field.column for field in through._meta.fields},
                         {'id', 'permissioncatalogentry_id', 'type_id'})


class RealmTypeSeedTests(TestCase):
    def test_all_seven_hardcoded_choices_are_seeded_and_readable(self):
        from community.models import Type
        self.assertEqual(set(Type.objects.values_list('code', flat=True)),
                         {'community', 'page', 'server', 'group', 'conference', 'channel', 'voice'})
        self.assertEqual(str(Type.objects.get(code='community')), 'Community')
