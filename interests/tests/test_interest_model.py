from django.db import IntegrityError
from django.test import TestCase

from interests.models import Interest


class InterestModelTests(TestCase):
    def test_normalized_name_computed_on_save(self):
        interest = Interest.objects.create(name="  Hiking  ")
        self.assertEqual(interest.normalized_name, "hiking")

    def test_normalized_name_uniqueness_blocks_case_variant_duplicate(self):
        Interest.objects.create(name="Travel")
        with self.assertRaises(IntegrityError):
            Interest.objects.create(name="travel")

    def test_get_or_create_by_name_is_idempotent_and_case_insensitive(self):
        first, created_first = Interest.objects.get_or_create_by_name("Outdoors")
        second, created_second = Interest.objects.get_or_create_by_name(" outdoors ")

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)

    def test_parent_set_null_on_delete(self):
        parent = Interest.objects.create(name="Sports")
        child = Interest.objects.create(name="Hiking", parent=parent)

        parent.delete()
        child.refresh_from_db()

        self.assertIsNone(child.parent)
