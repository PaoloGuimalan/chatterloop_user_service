import jwt
import os
import hashlib
import hmac
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()

ALGORITHMS = ["HS256"]
JWT_TOKEN = os.getenv("JWT_TOKEN")
# Server-only session signing key. JWT_TOKEN remains the client payload/nonce key.
AUTHENTICATION_SECRET = os.getenv("AUTHENTICATION_SECRET")


class JWTTools:

    def decoder(token, options=None):
        decode_value = jwt.decode(
            token, AUTHENTICATION_SECRET, algorithms=ALGORITHMS, options=options
        )
        return decode_value

    def encoder(value, *, authentication=True, device_token=None):
        expire_time = datetime.now(timezone.utc) + timedelta(
            days=30
        )  # 30 days expiration
        payload = {
            **value,
            "exp": expire_time,
            "iat": datetime.now(timezone.utc),
        }
        if authentication:
            if not isinstance(device_token, str) or not device_token:
                raise ValueError("Device token is required for authentication tokens")
            # Bind the signed token without disclosing the device credential.
            payload["hash"] = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
        key = AUTHENTICATION_SECRET if authentication else JWT_TOKEN
        encoded_value = jwt.encode(payload, key, algorithm=ALGORITHMS[0])
        return encoded_value

    def matches_device_hash(claim, device_token):
        if not isinstance(claim, str) or len(claim) != 64 or any(
            char not in "0123456789abcdef" for char in claim
        ):
            return False
        if not isinstance(device_token, str) or not device_token:
            return False
        actual = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(claim, actual)
