from ..models import Account, Verification
from django.utils.timezone import now
from .generators import make_id
from user_service.settings import MAILINGSERVICE
from django.conf import settings
from user_service.services.redis import RedisPubSubClient
from user_service.services.rabbitmq import RabbitMQClient, Queues
import requests


class PersistentEmailSender:
    """
    Queues mail for the Go worker to deliver.

    The class name and every method signature are unchanged: the SMTP round
    trip moved, the copy did not. Bodies are still rendered here, next to the
    FRONTEND_URL they link to, and the worker is a relay that takes an
    already-written message.

    What went away is _ensure_connection - an SMTP connection with a 30 second
    timeout, opened and reused on module-level mutable state shared across
    gunicorn workers, inside the request. One of its callers
    (send_contact_request_notification) ran inside an open database transaction,
    so a slow mail server held a Postgres transaction open with it.

    The cooldown checks stay here. They decide WHETHER to send, which is a
    per-pair rule the worker knows nothing about, and they must run before a
    message is queued rather than after it is delivered.
    """

    def _queue(self, to_email, subject, body, from_email):
        """Publish one rendered message. Returns False if there is nothing to send."""
        if not to_email:
            return False

        return RabbitMQClient.publish_on_commit(
            Queues.SEND_EMAIL,
            {
                "to": [to_email],
                "from": from_email,
                "subject": subject,
                "body": body,
            },
        ) is not False

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

        # Written BEFORE the message is queued, and not deferred: the code has to
        # exist the moment the user can type it, and it must survive the mail
        # failing. Mail is best-effort; the row is not.
        Verification.objects.create(
            user=Account.objects.get(username=user_id),
            ver_code=generated_code,
            date_generated=now(),
            is_used=False,
        )

        self._queue(to_email, subject, content, settings.EMAIL_VERIFY_USER)
        return True

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

        return self._queue(to_email, subject, content, settings.EMAIL_VERIFY_USER)

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
        requester's profile. Sent from EMAIL_HOST_USER (the noreply/notification
        identity) rather than EMAIL_VERIFY_USER, since this is a notification,
        not account verification.
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

        return self._queue(to_email, subject, content, settings.EMAIL_HOST_USER)

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
        accepted, linking to the accepter's profile.
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

        return self._queue(to_email, subject, content, settings.EMAIL_HOST_USER)

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
        Notifies a user that someone poked their profile. Pokes are a casual,
        repeatable action, so this uses a shorter cooldown than the
        contact-request email to still curb spam from someone poking the same
        profile over and over.
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

        return self._queue(to_email, subject, content, settings.EMAIL_HOST_USER)


# Usage: create one instance at module level
emailer = PersistentEmailSender()
