"""Run without databases: python -m unittest user.tests.test_jwt_key_separation."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import jwt


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
        token = self.tools.JWTTools.encoder({"userID": "account-1", "entity": "entity-1"})
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
