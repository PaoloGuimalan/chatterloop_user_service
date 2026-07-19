from django.contrib.auth.backends import BaseBackend
from .models import Account
from entity.models import Entity
from .utils.jwt_tools import JWTTools
from .utils.consent import get_pending_consents
from user_service.utils.crypto import decrypt_nonce
from user_service.services.redis import RedisPubSubClient
from .services.mongohelpers import SessionService
from django.core.exceptions import PermissionDenied
import time
import uuid

jwt = JWTTools

# Views a user must be able to reach even with an incomplete/non-compliant
# account, since these are exactly how they complete it (profile fields,
# email verification, consent acceptance).
COMPLIANCE_EXEMPT_VIEW_NAMES = {
    "api-user:user-management",
    "api-user:user-verification",
    "api-user:policy-document-list",
    "api-user:policy-consent-accept",
    "api-user:account-data-export",
    # The app shell (rail/top bar) needs allowed_modules/active_entity_context
    # to render around the Complete Profile / verification / consent pages
    # themselves, so this can't require a complete profile - it's just
    # module-visibility metadata, not something that depends on birthdate/
    # gender being set, and a profile-incomplete account is always the
    # personal entity anyway (never switched), so there's nothing sensitive
    # being gated here.
    "api-entity:my-allowed-modules",
}


class AutheticationBackend(BaseBackend):

    def _check_compliance(self, request, user):
        resolver_match = getattr(request, "resolver_match", None)
        if resolver_match is None:
            return

        view_name = resolver_match.view_name
        if view_name in COMPLIANCE_EXEMPT_VIEW_NAMES:
            return

        if user.is_minor():
            raise PermissionDenied(
                "ACCOUNT_UNDERAGE: account does not meet the minimum age requirement"
            )

        if not user.is_profile_complete():
            raise PermissionDenied(
                "PROFILE_INCOMPLETE: birthdate and gender are required"
            )

        if get_pending_consents(user.entity):
            raise PermissionDenied(
                "CONSENT_REQUIRED: latest Terms and Conditions must be accepted"
            )

    def authenticate(self, request):
        try:
            token = request.headers.get("x-access-token")
            origin = request.headers.get("origin")
            nonce = request.headers.get("x-nonce")
            device_token = request.headers.get("device-token")
            fcm_token = request.headers.get("fcm-token")

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

            if not user.is_active:
                raise PermissionDenied(
                    "ACCOUNT_INACTIVE: this account has been deactivated"
                )

            session = SessionService()
            is_existing = session.exists(device_token, decoded_header["entity"])

            if not is_existing:
                raise PermissionDenied("Device not logged in.")

            if fcm_token:
                session.update_fcm_token(
                    device_token, decoded_header["entity"], fcm_token
                )

            self._check_compliance(request, user)

            if decoded_header["entity"]:
                entity = Entity.objects.get(id=uuid.UUID(decoded_header["entity"]))
                request.entity = entity

            return (user, True)
        except Account.DoesNotExist:
            raise PermissionDenied("Account does not exist")
        except PermissionDenied:
            raise
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
