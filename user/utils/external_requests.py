from ..models import Account, Verification
from django.utils.timezone import now
from .generators import make_id
from user_service.settings import MAILINGSERVICE
from django.core.mail import send_mail
from django.conf import settings
import requests


from django.core.mail import get_connection
from django.conf import settings

from .entity import resolve_user_entity

class PersistentEmailSender:
    def __init__(self):
        self._connection = None

    def _ensure_connection(self):
        """Open or reuse a valid SMTP connection."""
        if self._connection is None:
            self._connection = get_connection(
                backend="django.core.mail.backends.smtp.EmailBackend",
                host=settings.EMAIL_HOST,
                port=settings.EMAIL_PORT,
                username=settings.EMAIL_VERIFY_USER,
                password=settings.EMAIL_VERIFY_PASS,
                use_tls=settings.EMAIL_USE_TLS,
                use_ssl=settings.EMAIL_USE_SSL,
                timeout=30,
            )
            self._connection.open()
        elif not getattr(self._connection, "_is_connected", False):
            # Reconnect if closed or stale
            self._connection.close()
            self._connection.open()
        self._connection._is_connected = True

    def send_email_verification_code(
        self,
        to_email: str,
        user_id: str,
        subject: str = "Verification Code",
        body: str = None,
    ):

        generated_code = make_id(6)
        content = body or f"""
Welcome to ChatterLoop!

Your registration was successful! Here is your verification code for the account activation: {generated_code}
            """.strip()

        self._ensure_connection()

        try:
            send_mail(
                subject=subject,
                message=content,
                from_email=settings.EMAIL_VERIFY_USER,
                recipient_list=[to_email],
                connection=self._connection,
            )

            Verification.objects.create(
                user=resolve_user_entity(Account.objects.get(username=user_id)),
                ver_code=generated_code,
                date_generated=now(),
                is_used=False,
            )
            return True

        except Exception as e:
            print("Error sending verification email:", e)
            self._connection._is_connected = False  # force reconnect next time
            return False

    def send_realm_invite_email(
        self,
        to_email: str,
        realm_name: str,
        invite_link: str,
        inviter_name: str = "Chatterloop",
        subject: str = "You're invited to a Chatterloop Realm",
        body: str = None,
    ):

        content = body or f"""
{inviter_name} invited you to join {realm_name}.

Open the invite link below to continue:
{invite_link}
            """.strip()

        self._ensure_connection()

        try:
            send_mail(
                subject=subject,
                message=content,
                from_email=settings.EMAIL_VERIFY_USER,
                recipient_list=[to_email],
                connection=self._connection,
            )
            return True
        except Exception as e:
            print("Error sending realm invite email:", e)
            self._connection._is_connected = False
            return False


# Usage: create one instance at module level
emailer = PersistentEmailSender()
