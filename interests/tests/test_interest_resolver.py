from datetime import timedelta

from django.test import TestCase
from django.utils.timezone import now

from entity.models import Entity
from entity.permissions import PermissionEffect
from interests.models import EntityInterest, EntityInterestAffinity, Interest
from interests.services.interest_resolver import (
    IMPLICIT_GRANT_THRESHOLD,
    ensure_grant_override,
    resolve_interest,
)


def _make_entity():
    return Entity.objects.create(type="user")


class ResolveInterestTests(TestCase):
    def setUp(self):
        self.entity = _make_entity()
        self.sports = Interest.objects.create(name="Sports")
        self.outdoors = Interest.objects.create(name="Outdoors", parent=self.sports)
        self.hiking = Interest.objects.create(name="Hiking", parent=self.outdoors)

    def test_no_override_no_affinity_returns_neutral(self):
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, "neutral")
        self.assertIsNone(matched)

    def test_direct_grant_on_exact_interest(self):
        EntityInterest.objects.create(entity=self.entity, interest=self.hiking, effect=PermissionEffect.GRANT)
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, PermissionEffect.GRANT)
        self.assertEqual(matched, self.hiking)

    def test_direct_deny_on_exact_interest(self):
        EntityInterest.objects.create(entity=self.entity, interest=self.hiking, effect=PermissionEffect.DENY)
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, PermissionEffect.DENY)
        self.assertEqual(matched, self.hiking)

    def test_grant_on_parent_covers_child(self):
        EntityInterest.objects.create(entity=self.entity, interest=self.outdoors, effect=PermissionEffect.GRANT)
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, PermissionEffect.GRANT)
        self.assertEqual(matched, self.outdoors)

    def test_specific_child_grant_overrides_broader_parent_deny(self):
        # The core departure from has_permission()'s "deny always beats
        # grant" - here the more specific level wins regardless of effect.
        EntityInterest.objects.create(entity=self.entity, interest=self.outdoors, effect=PermissionEffect.DENY)
        EntityInterest.objects.create(entity=self.entity, interest=self.hiking, effect=PermissionEffect.GRANT)
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, PermissionEffect.GRANT)
        self.assertEqual(matched, self.hiking)

    def test_specific_child_deny_overrides_broader_parent_grant(self):
        EntityInterest.objects.create(entity=self.entity, interest=self.outdoors, effect=PermissionEffect.GRANT)
        EntityInterest.objects.create(entity=self.entity, interest=self.hiking, effect=PermissionEffect.DENY)
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, PermissionEffect.DENY)
        self.assertEqual(matched, self.hiking)

    def test_expired_override_falls_through_to_next_ancestor_level(self):
        EntityInterest.objects.create(
            entity=self.entity,
            interest=self.hiking,
            effect=PermissionEffect.DENY,
            expires_at=now() - timedelta(minutes=1),
        )
        EntityInterest.objects.create(entity=self.entity, interest=self.outdoors, effect=PermissionEffect.GRANT)
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, PermissionEffect.GRANT)
        self.assertEqual(matched, self.outdoors)

    def test_implicit_affinity_above_threshold_grants_when_no_override(self):
        EntityInterestAffinity.objects.create(
            entity=self.entity, interest=self.hiking, score=IMPLICIT_GRANT_THRESHOLD
        )
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, "grant")
        self.assertIsNone(matched)

    def test_implicit_affinity_below_threshold_stays_neutral_never_deny(self):
        EntityInterestAffinity.objects.create(
            entity=self.entity, interest=self.hiking, score=IMPLICIT_GRANT_THRESHOLD - 1
        )
        effect, matched = resolve_interest(self.entity, self.hiking)
        self.assertEqual(effect, "neutral")

    def test_none_entity_or_interest_returns_neutral(self):
        self.assertEqual(resolve_interest(None, self.hiking), ("neutral", None))
        self.assertEqual(resolve_interest(self.entity, None), ("neutral", None))


class EnsureGrantOverrideTests(TestCase):
    def setUp(self):
        self.entity = _make_entity()
        self.hiking = Interest.objects.create(name="Hiking")
        self.camping = Interest.objects.create(name="Camping")

    def test_creates_grant_when_no_override_exists(self):
        ensure_grant_override(self.entity.id, [self.hiking.id])
        override = EntityInterest.objects.get(entity=self.entity, interest=self.hiking)
        self.assertEqual(override.effect, PermissionEffect.GRANT)
        self.assertEqual(override.created_by_id, str(self.entity.id))

    def test_does_not_overwrite_existing_deny(self):
        EntityInterest.objects.create(
            entity=self.entity, interest=self.hiking, effect=PermissionEffect.DENY
        )
        ensure_grant_override(self.entity.id, [self.hiking.id])
        override = EntityInterest.objects.get(entity=self.entity, interest=self.hiking)
        self.assertEqual(override.effect, PermissionEffect.DENY)

    def test_is_idempotent_and_handles_multiple_interests(self):
        ensure_grant_override(self.entity.id, [self.hiking.id, self.camping.id])
        ensure_grant_override(self.entity.id, [self.hiking.id, self.camping.id])
        self.assertEqual(
            EntityInterest.objects.filter(entity=self.entity).count(), 2
        )
