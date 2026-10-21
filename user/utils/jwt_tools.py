import jwt
import os
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

    def encoder(value, *, authentication=True):
        expire_time = datetime.now(timezone.utc) + timedelta(
            days=30
        )  # 30 days expiration
        payload = {
            **value,
            "exp": expire_time,
            "iat": datetime.now(timezone.utc),
        }
        key = AUTHENTICATION_SECRET if authentication else JWT_TOKEN
        encoded_value = jwt.encode(payload, key, algorithm=ALGORITHMS[0])
        return encoded_value
