"""Request transitions invalidate the correct realm views after commit."""
# ruff: noqa: E402

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from community.views import InviteView


class MembershipRefreshTests(unittest.TestCase):


    def test_servers_use_realm_request_signals(self):
        view = InviteView()
        realm = SimpleNamespace(type="server", realm_id="server")
        with (
            patch.object(view, "_realm_admin_recipient_ids", return_value={"admin"}),
            patch.object(view, "_publish_event") as publish,
        ):
            view._notify_requests_changed(realm)
        self.assertEqual(publish.call_args.args[1], "realm_requests_changed")
        self.assertEqual(publish.call_args.args[2]["realm_id"], "server")
