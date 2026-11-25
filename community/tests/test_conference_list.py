from datetime import timedelta

from django.test import TestCase
from django.utils.timezone import now
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Member
from community.tests.test_lifecycle_api import LifecycleFixture
from community.views import MyRealms
from entity.models import Entity
from entity.roles import get_default_role


class ConferenceListTests(LifecycleFixture, TestCase):
    def list_conferences(self, page=1, actor=None):
        request = APIRequestFactory().get('/my-list', {
            'type': 'conference', 'page': page, 'page_size': 1,
        })
        force_authenticate(request, user=self.account)
        request.entity = actor or self.actor
        response = MyRealms.as_view()(request)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_active_owned_and_member_conferences_are_newest_first_across_pages(self):
        created_at = now()
        another_owner = Entity.objects.create(type='user')
        older = self.make('conference', id='conference-z', slug='older-conference',
                          created_at=created_at - timedelta(days=1))
        joined = self.make('conference', id='conference-b', owner=another_owner,
                           slug='joined-conference', created_at=created_at)
        Member.objects.create(entity=self.actor, realm=joined, added_by=another_owner,
                              role=get_default_role('member', realm=joined))
        scheduled = self.make('conference', id='conference-c', slug='newest-conference',
                              created_at=created_at, starts_at=created_at + timedelta(days=1))
        self.make('conference', owner=another_owner, is_private=True)
        self.make('conference', is_active=False)
        self.make('conference', deletion_requested_at=now())

        pages = [self.list_conferences(page) for page in (1, 2, 3)]
        self.assertEqual(pages[0]['count'], 3)
        rows = [page['results'][0] for page in pages]
        self.assertEqual([row['id'] for row in rows], [scheduled.pk, joined.pk, older.pk])
        self.assertEqual([row['my_role'] for row in rows], ['owner', 'member', 'owner'])
        self.assertEqual([row['capabilities']['can_delete_realm'] for row in rows], [True, False, True])
        self.assertIsNotNone(rows[0]['starts_at'])
        self.assertIsNone(rows[1]['starts_at'])

    def test_switched_page_sees_only_its_conferences_and_can_delete_its_own(self):
        page = self.make('page')
        self.make('conference')
        owned = self.make('conference', owner=page.entity)

        result = self.list_conferences(actor=page.entity)

        self.assertEqual(result['count'], 1)
        self.assertEqual(result['results'][0]['id'], owned.pk)
        self.assertTrue(result['results'][0]['capabilities']['can_delete_realm'])
