import uuid

from django.test import TestCase

from community.models import Realm
from entity.models import Entity
from entity.utils import get_entity_display_name, get_entity_profile_path
from user.models import Account


class EntityDisplayNameTests(TestCase):
    def test_user_entity_display_name_and_profile_path(self):
        user_entity = Entity.objects.create(type="user")
        account = Account.objects.create(
            entity=user_entity,
            first_name="Test",
            last_name="User",
            email=f"{uuid.uuid4()}@example.com",
            username="testuser123",
        )
        self.assertEqual(get_entity_display_name(user_entity), "@testuser123")
        self.assertEqual(get_entity_profile_path(user_entity), "testuser123")

    def test_realm_entity_display_name_and_profile_path(self):
        realm_entity = Entity.objects.create(type="realm")
        creator_entity = Entity.objects.create(type="user")
        realm = Realm.objects.create(
            entity=realm_entity,
            name="Neon Systems",
            created_by=creator_entity,
            type="page",
        )
        realm.slug = "neon-systems"
        realm.save()

        self.assertEqual(get_entity_display_name(realm_entity), "Neon Systems")
        self.assertEqual(get_entity_profile_path(realm_entity), "neon-systems")

    def test_realm_entity_profile_path_falls_back_to_realm_id_without_slug(self):
        realm_entity = Entity.objects.create(type="realm")
        creator_entity = Entity.objects.create(type="user")
        realm = Realm.objects.create(
            entity=realm_entity,
            name="No Slug Page",
            created_by=creator_entity,
            type="page",
        )
        self.assertIsNone(realm.slug)
        self.assertEqual(get_entity_profile_path(realm_entity), realm.realm_id)

    def test_bare_entity_falls_back_to_id(self):
        bot_entity = Entity.objects.create(type="bot")
        self.assertEqual(get_entity_display_name(bot_entity), str(bot_entity.id))
        self.assertEqual(get_entity_profile_path(bot_entity), str(bot_entity.id))
