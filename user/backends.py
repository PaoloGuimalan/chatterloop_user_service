from django.contrib.auth.backends import BaseBackend
from .models import Account
from .utils.jwt_tools import JWTTools
from user_service.utils.crypto import decrypt_nonce
from user_service.services.redis import RedisPubSubClient
from .services.mongohelpers import SessionService
from django.core.exceptions import PermissionDenied
import time

jwt = JWTTools


class AutheticationBackend(BaseBackend):

    def authenticate(self, request):
        try:
            token = request.headers.get("x-access-token")
            origin = request.headers.get("origin")
            nonce = request.headers.get("x-nonce")
            device_token = request.headers.get("device-token")

            if not origin:
                raise PermissionDenied("Origin blocked")

            if not token:
                raise PermissionDenied("Token not defined")

            if not nonce:
                raise PermissionDenied("No Nonce defined")

            if not device_token:
                raise PermissionDenied("Device not recognized. Try logging in again.")

            decrypted = decrypt_nonce(nonce)

            if not decrypted:
                raise PermissionDenied("Error Nonce")

            now = int(time.time())
            if abs(now - decrypted["timestamp"]) > 60:
                raise PermissionDenied("Expired Nonce")

            is_valid = RedisPubSubClient.is_unique_nonce(
                decrypted["userId"], decrypted["timestamp"], decrypted["random"]
            )
            if not is_valid:
                raise PermissionDenied("Invalid Nonce")

            decoded_header = jwt.decoder(token)
            decoded_id = decoded_header["userID"]

            user = Account.objects.get(id=decoded_id)

            session = SessionService()
            is_existing = session.exists(device_token, user.id)

            if not is_existing:
                raise PermissionDenied("Device not logged in.")

            return (user, True)
        except Account.DoesNotExist:
            raise PermissionDenied("Account does not exist")
        except Exception as ex:
            print(ex)
            raise PermissionDenied("Error querying account")

    def get_user(self, user_id):
        try:
            return Account.objects.get(pk=user_id)
        except Account.DoesNotExist:
            return None

    def authenticate_header(self, request):
        return "Bearer"
