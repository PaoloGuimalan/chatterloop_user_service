import secrets

from django.db import transaction
from django.utils.timezone import now

from ..models import Connection, Verification
from ..ext_models.mongomodels import Message, Notification, Session


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
            deleted_at=now(), deleted_by=account
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
