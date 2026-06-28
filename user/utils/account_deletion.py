import secrets

from django.db import transaction
from django.utils.timezone import now

from ..models import Connection, Verification
from ..ext_models.mongomodels import Message, Notification, Session
from .entity import resolve_user_entity


def delete_account(account):
    """
    Anonymizes the account in place rather than hard-deleting the row.
    Most owned content (Realm.created_by, Post.user, Comment.user, ...)
    uses on_delete=DO_NOTHING, so a hard delete would either violate FK
    constraints or orphan other users' threads/realms. Anonymizing keeps
    the row (and its id) valid for those references while scrubbing the
    identifying information GDPR/CCPA erasure is actually about.
    """
    from community.models import Member, RealmFollow, Invite
    from diary.models import Entry
    from newsfeed.models import Post, Comment, Reaction, PostSave, PostTag, PostPrivacy

    account_id = account.id
    account_entity = resolve_user_entity(account)
    account_entity_id = account_entity.id

    with transaction.atomic():
        Post.objects.filter(user_id=account_entity_id, deleted_at__isnull=True).update(
            deleted_at=now(), deleted_by_id=account_entity_id
        )
        Comment.objects.filter(user_id=account_entity_id, deleted_at__isnull=True).update(
            deleted_at=now(), deleted_by_id=account_entity_id
        )
        Reaction.objects.filter(user_id=account_entity_id).delete()
        PostSave.objects.filter(user_id=account_entity_id).delete()
        PostTag.objects.filter(user_id=account_entity_id).delete()
        PostPrivacy.objects.filter(allowed_user_id=account_entity_id).delete()

        # Diary entries are private, non-shared content; safe to hard-delete
        # (cascades to Attachment/MapView).
        Entry.objects.filter(account_id=account_entity_id).delete()

        Member.objects.filter(account_id=account_entity_id).delete()
        RealmFollow.objects.filter(follower_id=account_entity_id).delete()
        Invite.objects.filter(target_user_id=account_entity_id).delete()
        Invite.objects.filter(created_by_id=account_entity_id).delete()

        Connection.objects.filter(action_by=account_entity).delete()
        Connection.objects.filter(involved_user=account_entity).delete()

        Verification.objects.filter(user=account_entity).delete()

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
    Message.objects(sender=str(account_id), isDeleted=False).update(
        set__isDeleted=True
    )
    Notification.objects(toUserID=str(account_id)).delete()
    Notification.objects(fromUserID=str(account_id)).delete()
    Session.objects(userID=str(account_id)).delete()

    return account
