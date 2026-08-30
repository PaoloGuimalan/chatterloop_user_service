import secrets

from django.db import transaction
from django.utils.timezone import now

from ..models import Connection, Verification
from ..ext_models.mongomodels import Message, Notification, Session
from user_service.services.redis import RedisPubSubClient


class DeletionChallenge:
    """
    Short-lived state for the public "delete my account" flow.

    Kept in Redis rather than in the Verification table on purpose. Verification
    rows are the REGISTRATION code, and CodeVerification accepts any unused row
    for a user - so a deletion code stored there could be typed into the
    registration screen and would activate the account instead. Separate storage
    makes the two impossible to confuse, expires by itself, and needs no
    migration.

    Everything here fails CLOSED: no Redis means no code can be issued and no
    token can be redeemed, so an outage blocks deletion rather than allowing an
    unverified one.
    """

    CODE_TTL = 15 * 60
    RESEND_COOLDOWN = 60
    MAX_ATTEMPTS = 5

    # The lookup step answers whether an address is registered and returns the
    # profile behind it, so it is an address checker by design. Capping it per
    # IP keeps that usable for the one person deleting their own account and
    # useless for running a mailing list through it.
    LOOKUP_WINDOW = 60 * 60
    LOOKUP_MAX_PER_IP = 10

    @staticmethod
    def _conn():
        return RedisPubSubClient.get_redis_connection()

    @staticmethod
    def _decode(value):
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    # ── issuing ──────────────────────────────────────────────────────────

    @classmethod
    def can_send(cls, email):
        """One code per address per minute, so this cannot be used to mailbomb."""
        conn = cls._conn()
        if not conn:
            return False
        key = f"chatterloop:acctdel:resend:{email}"
        return conn.set(key, "1", nx=True, ex=cls.RESEND_COOLDOWN) is True

    @classmethod
    def issue_code(cls, email):
        """A fresh code, replacing any outstanding one for this address."""
        conn = cls._conn()
        if not conn:
            return None

        code = "".join(secrets.choice("0123456789") for _ in range(6))
        conn.setex(f"chatterloop:acctdel:code:{email}", cls.CODE_TTL, code)
        conn.delete(f"chatterloop:acctdel:attempts:{email}")
        return code

    # ── verifying ────────────────────────────────────────────────────────

    @classmethod
    def register_attempt(cls, email):
        """
        Counts a guess. False once the code has been tried MAX_ATTEMPTS times,
        which also burns the code - a six-digit secret is only worth anything
        if it cannot be enumerated.
        """
        conn = cls._conn()
        if not conn:
            return False

        key = f"chatterloop:acctdel:attempts:{email}"
        attempts = conn.incr(key)
        if attempts == 1:
            conn.expire(key, cls.CODE_TTL)

        if attempts > cls.MAX_ATTEMPTS:
            conn.delete(f"chatterloop:acctdel:code:{email}")
            return False
        return True

    @classmethod
    def check_code(cls, email, code):
        conn = cls._conn()
        if not conn or not code:
            return False

        stored = cls._decode(conn.get(f"chatterloop:acctdel:code:{email}"))
        if stored is None:
            return False

        # Constant-time: a timing difference on a six-digit code is worth
        # avoiding even though the attempt counter already caps guessing.
        return secrets.compare_digest(stored, str(code))

    @classmethod
    def clear_code(cls, email):
        conn = cls._conn()
        if conn:
            conn.delete(f"chatterloop:acctdel:code:{email}")
            conn.delete(f"chatterloop:acctdel:attempts:{email}")

    # ── lookup throttling ────────────────────────────────────────────────

    @classmethod
    def can_lookup(cls, ip_address):
        """
        LOOKUP_MAX_PER_IP address checks an hour. A person deleting their own
        account needs one; anything enumerating needs thousands.

        Fails OPEN, unlike the rest of this class: Redis being down should not
        stop somebody deleting their account, and the code step behind this is
        what actually protects the deletion.
        """
        conn = cls._conn()
        if not conn or not ip_address:
            return True

        key = f"chatterloop:acctdel:lookup:{ip_address}"
        count = conn.incr(key)
        if count == 1:
            conn.expire(key, cls.LOOKUP_WINDOW)

        return count <= cls.LOOKUP_MAX_PER_IP


def delete_account(account, entity):
    """
    Anonymizes the account in place rather than hard-deleting the row.
    Most owned content (Realm.created_by, Post.user, Comment.user, ...)
    uses on_delete=DO_NOTHING, so a hard delete would either violate FK
    constraints or orphan other users' threads/realms. Anonymizing keeps
    the row (and its id) valid for those references while scrubbing the
    identifying information GDPR/CCPA erasure is actually about.
    """
    from community.models import Member, Follow, Invite
    from diary.models import Entry
    from newsfeed.models import (
        Post,
        Comment,
        CommentReaction,
        Reaction,
        PostSave,
        PostTag,
        PostPrivacy,
    )

    account_id = account.id

    with transaction.atomic():
        Post.objects.filter(entity=entity, deleted_at__isnull=True).update(
            deleted_at=now(), deleted_by=entity
        )
        Comment.objects.filter(entity=entity, deleted_at__isnull=True).update(
            deleted_at=now(), deleted_by=entity
        )
        Reaction.objects.filter(entity=entity).delete()
        # Mirrors the line above, including its known gap: neither decrements
        # the matching PreviewCount / CommentPreviewCount tally, so an erased
        # account leaves its reactions counted. Rare enough to have been lived
        # with on the post side; worth fixing on both together, not one.
        CommentReaction.objects.filter(entity=entity).delete()
        PostSave.objects.filter(entity=entity).delete()
        PostTag.objects.filter(entity=entity).delete()
        PostPrivacy.objects.filter(allowed_entity=entity).delete()

        # Diary entries are private, non-shared content; safe to hard-delete
        # (cascades to Attachment/MapView).
        Entry.objects.filter(account=account).delete()

        Member.objects.filter(entity=entity).delete()
        Follow.objects.filter(follower=entity).delete()
        Invite.objects.filter(target_entity=entity).delete()
        Invite.objects.filter(created_by=entity).delete()

        Connection.objects.filter(action_by=entity).delete()
        Connection.objects.filter(involved_entity=entity).delete()

        Verification.objects.filter(user=account).delete()

        anon_suffix = secrets.token_hex(8)
        account.username = f"deleted_{anon_suffix}"
        account.first_name = "Deleted"
        account.middle_name = "N/A"
        account.last_name = "User"
        account.email = f"deleted-{anon_suffix}@chatterloop.invalid"
        account.password = secrets.token_hex(32)
        account.birthdate = None
        account.gender = None
        account.profile = "none"
        account.coverphoto = "none"
        account.is_active = False
        account.save()

    # Mongo isn't part of the Postgres transaction; best-effort cleanup.
    Message.objects(sender=str(entity.id), isDeleted=False).update(set__isDeleted=True)
    Notification.objects(toUserID=str(entity.id)).delete()
    Notification.objects(fromUserID=str(entity.id)).delete()
    Session.objects(entityID=str(entity.id)).delete()

    return account
