"""Run without databases: python -m unittest user.tests.test_jwt_key_separation."""
import importlib.util
import os
import sys
import time
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch

import jwt
from django.core.exceptions import PermissionDenied


CLIENT_SECRET = "client-request-test-key-not-for-authentication"
AUTHENTICATION_SECRET = "server-only-authentication-test-key-different"


class JwtKeySeparationTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "utils" / "jwt_tools.py"
        spec = importlib.util.spec_from_file_location("isolated_jwt_tools", path)
        self.tools = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {
            "JWT_TOKEN": CLIENT_SECRET,
            "AUTHENTICATION_SECRET": AUTHENTICATION_SECRET,
        }):
            spec.loader.exec_module(self.tools)

    def test_login_tokens_use_server_only_authentication_key(self):
        token = self.tools.JWTTools.encoder({"userID": "account-1", "entity": "entity-1"}, device_token="abc")
        self.assertEqual(jwt.decode(token, AUTHENTICATION_SECRET, algorithms=["HS256"])["userID"], "account-1")
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(token, CLIENT_SECRET, algorithms=["HS256"])

    def test_authentication_rejects_tokens_created_with_client_key(self):
        token = jwt.encode({"userID": "account-1", "entity": "entity-1"}, CLIENT_SECRET, algorithm="HS256")
        with self.assertRaises(jwt.InvalidSignatureError):
            self.tools.JWTTools.decoder(token)

    def test_authentication_accepts_server_issued_tokens(self):
        token = jwt.encode({"userID": "account-1", "entity": "entity-1"}, AUTHENTICATION_SECRET, algorithm="HS256")
        self.assertEqual(self.tools.JWTTools.decoder(token)["entity"], "entity-1")

    def test_profile_response_remains_verifiable_with_client_key(self):
        token = self.tools.JWTTools.encoder({"username": "person"}, authentication=False)
        self.assertEqual(jwt.decode(token, CLIENT_SECRET, algorithms=["HS256"])["username"], "person")
        with self.assertRaises(jwt.InvalidSignatureError):
            self.tools.JWTTools.decoder(token)

    def test_authentication_claim_is_sha256_of_device_and_cannot_be_overridden(self):
        token = self.tools.JWTTools.encoder({"userID": "account-1", "hash": "forged"}, device_token="abc")
        claims = self.tools.JWTTools.decoder(token)
        self.assertEqual(claims["hash"], "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertNotIn("device_token", claims)

    def test_authentication_issuance_requires_device(self):
        for device in [None, "", 123, [], {}]:
            with self.subTest(device=device), self.assertRaises(ValueError):
                self.tools.JWTTools.encoder({"userID": "account-1"}, device_token=device)

    def test_device_hash_matching_rejects_other_devices_and_missing_claims(self):
        claim = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        self.assertTrue(self.tools.JWTTools.matches_device_hash(claim, "abc"))
        self.assertFalse(self.tools.JWTTools.matches_device_hash(claim, "other-device"))
        for invalid in [None, "", 123, [], {}, "0" * 64, "é" * 64]:
            with self.subTest(invalid=invalid):
                self.assertFalse(self.tools.JWTTools.matches_device_hash(invalid, "abc"))
                self.assertFalse(self.tools.JWTTools.matches_device_hash(claim, invalid))

    def authenticate_request(self, device="abc", session_exists=True, token=None):
        """Exercise the real backend with database/cache boundaries kept in memory."""
        user = SimpleNamespace(is_active=True)
        entity = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
        sessions = SimpleNamespace(
            exists=lambda device, entity_id: session_exists and device in ("abc", "other-device") and entity_id == entity.id,
            update_fcm_token=lambda *args: None,
        )
        account = SimpleNamespace(objects=SimpleNamespace(get=lambda **kwargs: user), DoesNotExist=type("DoesNotExist", (Exception,), {}))
        dependencies = {
            "django.contrib.auth.backends": SimpleNamespace(BaseBackend=object),
            "user.models": SimpleNamespace(Account=account),
            "entity.models": SimpleNamespace(Entity=SimpleNamespace(objects=SimpleNamespace(get=lambda **kwargs: entity))),
            "user.utils.jwt_tools": self.tools,
            "user.utils.consent": SimpleNamespace(get_pending_consents=lambda _: []),
            "user_service.utils.crypto": SimpleNamespace(decrypt_nonce=lambda _: {"userId": "account-1", "timestamp": int(time.time()), "random": "unique"}),
            "user_service.services.redis": SimpleNamespace(RedisPubSubClient=SimpleNamespace(is_unique_nonce=lambda *args: True)),
            "user.services.mongohelpers": SimpleNamespace(SessionService=lambda: sessions),
        }
        path = Path(__file__).resolve().parents[1] / "backends.py"
        spec = importlib.util.spec_from_file_location("user.isolated_backends", path)
        backend = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(backend)
        token = token or self.tools.JWTTools.encoder({"userID": "account-1", "entity": entity.id}, device_token="abc")
        request = SimpleNamespace(headers={"x-access-token": token, "origin": "https://chatterloop.app", "x-nonce": "nonce", "device-token": device}, _request=SimpleNamespace())
        result = backend.AutheticationBackend().authenticate(request)
        self.assertIs(result[0], user)
        self.assertIs(request.entity, entity)
        self.assertIs(request._request.entity, entity)

    def test_backend_accepts_bound_token_with_registered_session(self):
        self.authenticate_request()

    def test_backend_rejects_copied_token_on_another_registered_device(self):
        with self.assertRaises(PermissionDenied):
            self.authenticate_request(device="other-device")

    def test_backend_still_rejects_revoked_device_session(self):
        with self.assertRaises(PermissionDenied):
            self.authenticate_request(session_exists=False)

    def test_backend_rejects_legacy_token_without_device_binding(self):
        token = jwt.encode({"userID": "account-1", "entity": "00000000-0000-0000-0000-000000000001"}, AUTHENTICATION_SECRET, algorithm="HS256")
        with self.assertRaises(PermissionDenied):
            self.authenticate_request(token=token)
