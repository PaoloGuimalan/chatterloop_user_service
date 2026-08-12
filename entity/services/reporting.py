"""Report target resolution.

One job: turn a client-supplied ``(target_type, target_id)`` pair into the
Entity that moderation should act on, plus the normalised target id to store.

Every branch returns an Entity, which is what makes reporting generic - a
profile report, a page report and a post report all land on the same column
and the moderation queue never has to care which kind it started as.
"""

from entity.models import Entity, EntityType, Report


class ReportTargetError(Exception):
    """Target could not be resolved. Carries the client-facing message."""

    def __init__(self, message, not_found=False):
        super().__init__(message)
        self.message = message
        self.not_found = not_found


def _entity_or_error(entity_id, expected_type, label):
    if not entity_id:
        raise ReportTargetError("target_id is required")
    entity = Entity.objects.filter(id=str(entity_id)).first()
    if entity is None:
        raise ReportTargetError(f"{label} not found", not_found=True)
    if expected_type and entity.type != expected_type:
        raise ReportTargetError(f"That target is not a {label.lower()}")
    return entity


def _resolve_user(target_id):
    # Clients send an entity id here (both the webapp profile and the Flutter
    # profile pass entityID), so resolve against Entity directly rather than
    # against Account.
    return _entity_or_error(target_id, EntityType.USER_CHOICE, "Account"), None


def _resolve_realm(target_id):
    # Pages, servers, groups and channels are all Realms. Accept either the
    # realm's entity id or its own pk - profile screens hold the entity id,
    # while server/member screens tend to hold the realm id, and making the
    # caller know which one it has buys nothing.
    from community.models import Realm

    if not target_id:
        raise ReportTargetError("target_id is required")

    target_id = str(target_id)
    entity = Entity.objects.filter(
        id=target_id, type=EntityType.REALM_CHOICE
    ).first()
    if entity is not None:
        return entity, None

    realm = Realm.objects.filter(id=target_id).select_related("entity").first()
    if realm is None:
        raise ReportTargetError("Realm not found", not_found=True)
    return realm.entity, None


def _resolve_post(target_id):
    from newsfeed.models import Post

    post = (
        Post.objects.filter(post_id=str(target_id), deleted_at__isnull=True)
        .select_related("entity")
        .first()
    )
    if post is None:
        raise ReportTargetError("Post not found", not_found=True)
    # str(), not the raw pk: these ids are CharFields defaulting to uuid4, so
    # an in-memory instance carries a UUID object while a fetched one carries
    # a string. The dedupe lookup in create_report keys off this value, so it
    # has to be the same shape every time.
    return post.entity, str(post.post_id)


def _resolve_comment(target_id):
    from newsfeed.models import Comment

    comment = (
        Comment.objects.filter(comment_id=str(target_id), deleted_at__isnull=True)
        .select_related("entity")
        .first()
    )
    if comment is None:
        raise ReportTargetError("Comment not found", not_found=True)
    return comment.entity, str(comment.comment_id)


def _resolve_message(target_id):
    from user.ext_models.mongomodels import Message
    from user.models import Account

    message_doc = Message._get_collection().find_one({"messageID": str(target_id)})
    if not message_doc:
        raise ReportTargetError("Message not found", not_found=True)

    sender_id = str(message_doc.get("sender") or "")
    # Mongo stores whatever the Node server wrote; historically that is an
    # Account id, but entity ids appear on newer rows. Try both before giving
    # up rather than guessing.
    entity = Entity.objects.filter(id=sender_id).first()
    if entity is None:
        account = (
            Account.objects.filter(id=sender_id).select_related("entity").first()
        )
        entity = account.entity if account else None
    if entity is None:
        raise ReportTargetError("Message sender not found", not_found=True)
    return entity, str(message_doc["messageID"])


_RESOLVERS = {
    "user": _resolve_user,
    "realm": _resolve_realm,
    "post": _resolve_post,
    "comment": _resolve_comment,
    "message": _resolve_message,
}


def resolve_report_target(target_type, target_id):
    """Return ``(reported_entity, normalised_target_id)``.

    ``normalised_target_id`` is None for entity-level target types, so a
    profile/realm report is stored one way only and can't be duplicated by a
    client that helpfully echoes the entity id back into target_id.
    """
    resolver = _RESOLVERS.get(target_type)
    if resolver is None:
        raise ReportTargetError("Invalid target_type")
    if not target_id:
        raise ReportTargetError("target_id is required")
    return resolver(target_id)


def create_report(reporter_entity, target_type, target_id, reason, description=""):
    """Validate and persist one report. Raises ReportTargetError on bad input.

    Returns ``(report, created)``. ``created`` is False when this reporter
    already has an open report against the same target - re-reporting is a
    no-op rather than an error, because from the reporter's side the outcome
    is identical and telling them "you already reported this" only leaks that
    the earlier one hasn't been actioned yet.
    """
    if target_type not in dict(Report.TARGET_TYPE_CHOICES):
        raise ReportTargetError("Invalid target_type")
    if reason not in dict(Report.REASON_CHOICES):
        raise ReportTargetError("Invalid reason")

    reported_entity, normalised_target_id = resolve_report_target(
        target_type, target_id
    )

    if str(reported_entity.id) == str(reporter_entity.id):
        raise ReportTargetError("You cannot report yourself")

    existing = Report.objects.filter(
        reporter=reporter_entity,
        reported_entity=reported_entity,
        target_type=target_type,
        target_id=normalised_target_id,
        status="pending",
    ).first()
    if existing is not None:
        return existing, False

    report = Report.objects.create(
        reporter=reporter_entity,
        reported_entity=reported_entity,
        target_type=target_type,
        target_id=normalised_target_id,
        reason=reason,
        description=(description or "")[:5000],
    )
    return report, True
