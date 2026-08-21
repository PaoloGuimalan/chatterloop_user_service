"""
Guards the LOGGING configuration in user_service/settings.py.

The failure this protects against is silent: if `django.request` is ever
routed back to `mail_admins`, or the console handler is dropped, nothing
breaks and no test fails - errors simply stop appearing in the container log
again. These tests assert the records are actually emitted.
"""

import logging

from django.test import TestCase, override_settings


@override_settings(ALLOWED_HOSTS=["*"])
class ErrorLoggingTests(TestCase):
    def test_swallowed_exception_is_logged_with_traceback(self):
        """
        An unparseable token makes JWTTools.decoder raise inside
        ThirdPartyAuthentication's broad `except Exception`, which returns a
        500 rather than letting the exception propagate. Django never sees
        the exception, so the traceback has to come from the view's own
        logger.exception() call.
        """
        with self.assertLogs(level=logging.ERROR) as captured:
            response = self.client.post(
                "/api/user/tp_auth",
                {"token": "not-a-real-jwt"},
                content_type="application/json",
                HTTP_DEVICE_TOKEN="logging-test-device",
            )

        self.assertEqual(response.status_code, 500)

        joined = "\n".join(captured.output)
        self.assertIn("Third-party authentication failed", joined)
        self.assertIn("Traceback (most recent call last)", joined)
        # The frame that actually failed, not just the exception message -
        # this is precisely what print(e) used to throw away.
        self.assertIn("user/views.py", joined.replace("\\", "/"))

    def test_server_error_response_is_logged_with_request_context(self):
        """
        ApiFailureLogMiddleware supplies what Django's own
        "Internal Server Error: /path" line omits: method, status, actor,
        client IP and the response body carrying str(e).
        """
        with self.assertLogs(
            "user_service.logging_middleware", level=logging.ERROR
        ) as captured:
            self.client.post(
                "/api/user/tp_auth",
                {"token": "not-a-real-jwt"},
                content_type="application/json",
                HTTP_DEVICE_TOKEN="logging-test-device",
            )

        joined = "\n".join(captured.output)
        self.assertIn("POST /api/user/tp_auth -> 500", joined)
        self.assertIn("actor=anonymous", joined)
        self.assertIn("Not enough segments", joined)
        # 5xx must be ERROR so a server fault stays distinguishable
        # from a merely rejected request.
        self.assertTrue(any(r.startswith("ERROR:") for r in captured.output))

    def test_sensitive_keys_are_redacted_from_logged_bodies(self):
        from user_service.logging_middleware import _describe_body

        class _FakeResponse:
            data = {"message": "boom", "authtoken": "super-secret", "token": "abc"}

        described = _describe_body(_FakeResponse())
        self.assertIn("boom", described)
        self.assertNotIn("super-secret", described)
        self.assertNotIn("abc", described)
        self.assertIn("<redacted>", described)

    def test_client_error_is_logged_at_warning(self):
        """
        Django logs 4xx on `django.request` at WARNING, but the LOGGING config
        pins that logger to ERROR, so before this middleware no client error
        was recorded anywhere.
        """
        with self.assertLogs(
            "user_service.logging_middleware", level=logging.WARNING
        ) as captured:
            response = self.client.get(
                "/api/user/contacts",
                HTTP_DEVICE_TOKEN="logging-test-device",
            )

        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)

        joined = chr(10).join(captured.output)
        self.assertIn("-> " + str(response.status_code), joined)
        # WARNING, not ERROR - a rejected request is not a server fault.
        self.assertTrue(any(r.startswith("WARNING:") for r in captured.output))
        self.assertFalse(any(r.startswith("ERROR:") for r in captured.output))

    def test_declared_failure_body_is_recognised(self):
        """
        The codebase signals failure with `{"status": False}`, occasionally
        alongside a 2xx that no status-code rule would catch on its own.
        """
        from user_service.logging_middleware import _is_declared_failure

        class _Ok:
            data = {"status": True, "result": {}}

        class _DeclaredFailure:
            data = {"status": False, "message": "nope"}

        class _NotADict:
            data = "plain string body"

        self.assertTrue(_is_declared_failure(_DeclaredFailure()))
        self.assertFalse(_is_declared_failure(_Ok()))
        self.assertFalse(_is_declared_failure(_NotADict()))
