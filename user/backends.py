from django.contrib.auth.backends import BaseBackend
from .models import Account
from .utils.jwt_tools import JWTTools
from user_service.utils.crypto import decrypt_nonce
from user_service.services.redis import RedisPubSubClient
import time

jwt = JWTTools


class AutheticationBackend(BaseBackend):

    def authenticate(self, request):
        try:
            token = request.headers.get("x-access-token")
            origin = request.headers.get("origin")
            nonce = request.headers.get("x-nonce")

            if not origin:
                return None

            if not token:
                return None

            if not nonce:
                return None

            decrypted = decrypt_nonce(nonce)

            if not decrypted:
                return None

            now = int(time.time())
            if abs(now - decrypted["timestamp"]) > 60:
                return None

            is_valid = RedisPubSubClient.is_unique_nonce(
                decrypted["userId"], decrypted["timestamp"], decrypted["random"]
            )
            if not is_valid:
                return None

            decoded_header = jwt.decoder(token)
            decoded_id = decoded_header["userID"]

            user = Account.objects.get(id=decoded_id)
            return (user, True)
        except Account.DoesNotExist:
            return None
        except:
            return None

    def get_user(self, user_id):
        try:
            return Account.objects.get(pk=user_id)
        except Account.DoesNotExist:
            return None

    def authenticate_header(self, request):
        return "Bearer"
