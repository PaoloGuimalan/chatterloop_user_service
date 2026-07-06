from ..models import Account, Verification
from django.utils.timezone import now
from .generators import make_id
from user_service.settings import MAILINGSERVICE
from django.core.mail import send_mail
from django.conf import settings
from user_service.services.redis import RedisPubSubClient
import requests


from django.core.mail import get_connection
from django.conf import settings


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
Hi {user_id}. Welcome to Chatterloop!

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
                user=Account.objects.get(username=user_id),
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

    def send_contact_request_notification(
        self,
        to_email: str,
        from_entity_id: str,
        to_entity_id: str,
        from_username: str,
        subject: str = "You have a new contact request on Chatterloop",
        body: str = None,
    ):
        """
        Notifies a user that someone added them as a contact, linking to the
        requester's profile. Uses EMAIL_HOST_USER (the noreply/notification
        credentials) rather than EMAIL_VERIFY_USER, since this is a
        notification email, not an account-verification one.
        """

        if not RedisPubSubClient.acquire_email_cooldown(
            "contact_request", from_entity_id, to_entity_id
        ):
            return False

        profile_link = f"{settings.FRONTEND_URL}/{from_username}"
        content = body or f"""
@{from_username} has added you as a contact on Chatterloop.

View their profile here:
{profile_link}
            """.strip()

        try:
            send_mail(
                subject=subject,
                message=content,
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[to_email],
            )
            return True
        except Exception as e:
            print("Error sending contact request notification email:", e)
            return False

    def send_contact_accepted_email(
        self,
        to_email: str,
        from_entity_id: str,
        to_entity_id: str,
        from_username: str,
        subject: str = "Your contact request was accepted",
        body: str = None,
    ):
        """
        Notifies the original requester that their contact request was
        accepted, linking to the accepter's profile. Uses EMAIL_HOST_USER,
        same as the other notification emails.
        """

        if not RedisPubSubClient.acquire_email_cooldown(
            "contact_accepted", from_entity_id, to_entity_id
        ):
            return False

        profile_link = f"{settings.FRONTEND_URL}/{from_username}"
        content = body or f"""
@{from_username} accepted your contact request on Chatterloop.

View their profile here:
{profile_link}
            """.strip()

        try:
            send_mail(
                subject=subject,
                message=content,
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[to_email],
            )
            return True
        except Exception as e:
            print("Error sending contact accepted email:", e)
            return False

    def send_poke_notification_email(
        self,
        to_email: str,
        from_entity_id: str,
        to_entity_id: str,
        from_username: str,
        subject: str = "Someone poked you on Chatterloop",
        body: str = None,
        cooldown_ttl: int = 3600,
    ):
        """
        Notifies a user that someone poked their profile. Uses
        EMAIL_HOST_USER, same as other notification emails. Pokes are a
        casual, repeatable action, so this uses a shorter cooldown than the
        contact-request email to still curb spam from someone poking
        the same profile over and over.
        """

        if not RedisPubSubClient.acquire_email_cooldown(
            "poke", from_entity_id, to_entity_id, ttl=cooldown_ttl
        ):
            return False

        profile_link = f"{settings.FRONTEND_URL}/{from_username}"
        content = body or f"""
@{from_username} just poked you on Chatterloop.

View their profile here:
{profile_link}
            """.strip()

        try:
            send_mail(
                subject=subject,
                message=content,
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[to_email],
            )
            return True
        except Exception as e:
            print("Error sending poke notification email:", e)
            return False


# Usage: create one instance at module level
emailer = PersistentEmailSender()
