from django.test import TestCase

from community.models import Realm
from user.models import Account

from entity.models import Entity, build_entity_id
from entity.services import (
    entity_for_account,
    entity_for_realm,
    parse_entity_id,
    resolve_entities,
    resolve_entity,
)


class BuildEntityIdTests(TestCase):
    def test_format_is_deterministic(self):
        self.assertEqual(build_entity_id("user", "abc"), "entity:user:abc")
        self.assertEqual(build_entity_id("realm", "123"), "entity:realm:123")

    def test_type_is_lowercased_and_stripped(self):
        self.assertEqual(build_entity_id("  USER ", "abc"), "entity:user:abc")

    def test_parse_round_trip(self):
        eid = build_entity_id("realm", "987654321012345")
        self.assertEqual(parse_entity_id(eid), ("realm", "987654321012345"))

    def test_parse_rejects_malformed(self):
        self.assertEqual(parse_entity_id("nope"), (None, None))
        self.assertEqual(parse_entity_id(""), (None, None))
        self.assertEqual(parse_entity_id(None), (None, None))


class EntitySignalTests(TestCase):
    def test_account_create_mints_user_entity(self):
        account = Account.objects.create(
            first_name="Ada", last_name="Lovelace", email="ada@example.com"
        )
        eid = build_entity_id("user", str(account.id))
        entity = Entity.objects.get(entity_id=eid)
        self.assertEqual(entity.entity_type, Entity.ENTITY_TYPE_USER)
        self.assertEqual(entity.source_type, Entity.SOURCE_TYPE_ACCOUNT)
        self.assertEqual(entity.source_id, str(account.id))

    def test_realm_create_mints_realm_entity(self):
        creator = Account.objects.create(
            first_name="Grace", last_name="Hopper", email="grace@example.com"
        )
        realm = Realm.objects.create(name="Cobol", created_by=creator, type="page")
        eid = build_entity_id("realm", str(realm.realm_id))
        entity = Entity.objects.get(entity_id=eid)
        self.assertEqual(entity.entity_type, Entity.ENTITY_TYPE_REALM)
        self.assertEqual(entity.source_id, str(realm.realm_id))

    def test_helpers_are_idempotent(self):
        account = Account.objects.create(
            first_name="Alan", last_name="Turing", email="alan@example.com"
        )
        first = entity_for_account(account)
        second = entity_for_account(account)
        self.assertEqual(first.entity_id, second.entity_id)
        self.assertEqual(
            Entity.objects.filter(source_id=str(account.id)).count(), 1
        )


class ResolveTests(TestCase):
    def test_resolve_entity_returns_source(self):
        account = Account.objects.create(
            first_name="Edsger", last_name="Dijkstra", email="ed@example.com"
        )
        realm = Realm.objects.create(name="Algorithms", created_by=account, type="page")
        acc_eid = build_entity_id("user", str(account.id))
        realm_eid = build_entity_id("realm", str(realm.realm_id))

        self.assertEqual(str(resolve_entity(acc_eid).id), str(account.id))
        self.assertEqual(resolve_entity(realm_eid).realm_id, realm.realm_id)
        self.assertIsNone(resolve_entity("entity:user:missing"))
        self.assertIsNone(resolve_entity("garbage"))

    def test_resolve_entities_batch(self):
        account = Account.objects.create(
            first_name="Linus", last_name="Torvalds", email="linus@example.com"
        )
        realm = Realm.objects.create(name="Kernel", created_by=account, type="server")
        acc_eid = entity_for_account(account).entity_id
        realm_eid = entity_for_realm(realm).entity_id

        resolved = resolve_entities([acc_eid, realm_eid, "entity:user:missing"])
        self.assertEqual(str(resolved[acc_eid].id), str(account.id))
        self.assertEqual(resolved[realm_eid].realm_id, realm.realm_id)
        self.assertNotIn("entity:user:missing", resolved)
