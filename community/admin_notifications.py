"""Community management inbox and per-administrator bell projections."""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from django.db.models import Q
from django.shortcuts import get_object_or_404
from bson import ObjectId
from mongoengine import NotUniqueError
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.utils.urls import replace_query_param

from community.models import Invite, Member, Realm
from entity.models import Entity
from entity.permissions import Permission
from entity.serializers import EntitySerializer
from entity.services.permission_resolver import has_permission
from entity.utils import get_entity_display_username, get_entity_name
from newsfeed.models import Comment, CommentReaction, Post, PostTag, Reaction
from user.ext_models.mongomodels import (
    Content,
    DateInfo,
    Notification,
    NotificationTarget,
)
from user_service.services.redis import RedisPubSubClient

logger = logging.getLogger(__name__)
JOIN_REQUEST = "community_join_request"
ADMIN_ACTIVITY = "community_admin_activity"
ADMIN_INBOX = "community_admin_inbox"
PREVIEW_HEADLINE = "Community activity preview"
MEMBER_JOINED = "community_member_joined"
POST_CREATED = "community_post_created"
INBOX_TYPES = (
    JOIN_REQUEST,
    MEMBER_JOINED,
    POST_CREATED,
    "post_comment",
    "post_reaction",
    "comment_reaction",
)
MANAGER_ROLES = ("owner", "admin", "moderator")


def _managers(realm):
    return list(
        Member.objects.filter(realm=realm, role__in=MANAGER_ROLES).values_list(
            "entity_id", flat=True
        )
    )


def _redirects(realm):
    return [
        {"platform": platform, "type": "community_notifications", "route": route}
        for platform, route in (
            ("web", f"/realms/{realm.pk}/notifications"),
            ("android", f"/realm/{realm.pk}/manage/notifications"),
            ("ios", f"/realm/{realm.pk}/manage/notifications"),
        )
    ]


def _notice_id(reference, recipient, kind):
    return "NTF_community_admin_" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{kind}:{reference}:{recipient}")
    )


def _publish_event(realm, recipient, event, *, personal=False):
    RedisPubSubClient.publish_json(
        f"events_{recipient}",
        {
            "logType": None,
            "pod": "podless",
            "event": event,
            "message": {
                "status": True,
                "auth": True,
                "message": "Community notifications changed",
                "result": {
                    "realm_id": str(realm.pk),
                    "community_entity_id": str(realm.entity_id),
                    **({"type": ADMIN_ACTIVITY} if personal else {}),
                },
            },
            "dateTime": datetime.now().astimezone().isoformat(),
        },
    )


def _event(realm, recipients):
    for recipient in recipients:
        _publish_event(realm, recipient, "community_notifications_changed")


def _upsert_notice(**notice):
    """Return true only for the first delivery of this event to this inbox."""
    notification_id = _notice_id(
        notice["referenceID"], notice["toUserID"], notice["type"]
    )
    fields = {
        key: notice[key]
        for key in (
            "referenceID",
            "toUserID",
            "fromUserID",
            "type",
            "is_anonymous",
        )
    }
    fields.update(
        content=Content(
            headline=notice["content_headline"], details=notice["content_details"]
        ),
        target=NotificationTarget(
            type=notice["target_type"],
            supportingID=str(notice["target_id"]),
            anchor=notice.get("target_anchor"),
        ),
        referenceStatus=notice.get("referenceStatus", True),
        redirects=notice.get("redirects"),
    )
    updates = {f"set__{key}": value for key, value in fields.items()}
    updates.update(
        set_on_insert__date=DateInfo(date=_timestamp()),
        set_on_insert__isRead=notice.get("isRead", False),
    )
    try:
        result = Notification.objects(notificationID=notification_id).update_one(
            upsert=True, full_result=True, **updates
        )
        return result.upserted_id is not None
    except NotUniqueError:
        # A competing first delivery won the unique notificationID. Refresh
        # its safe projection, but do not create a second inbox entry.
        Notification.objects(notificationID=notification_id).update_one(**updates)
        return False


def _timestamp():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _admin_inbox(realm, recipient):
    return Notification.objects(
        type=ADMIN_INBOX,
        toUserID=str(recipient),
        referenceID__in=Notification.objects(
            toUserID=str(realm.entity_id), type__in=INBOX_TYPES
        ).distinct("notificationID"),
    )


def _save_preview(realm, recipient):
    """Replace the saved preview immediately using existing notification fields."""
    manager = Entity.objects.get(pk=recipient)
    if not has_permission(manager, Permission.REALM_REQUEST_APPROVE, realm=realm):
        return
    # The existing Mongo _id is the unique slot, shared with the Node writer.
    # Concurrent activity replaces this row instead of deleting another write.
    slot_key = json.dumps(
        ["community_admin_preview", str(realm.entity_id), str(recipient)],
        separators=(",", ":"),
    )
    slot_id = ObjectId(hashlib.sha256(slot_key.encode()).hexdigest()[:24])
    while True:
        previous = Notification.objects(id=slot_id).first()
        previous_id = previous.notificationID if previous else None
        cutoff = _timestamp()
        unread = _admin_inbox(realm, recipient).filter(
            isRead=False, date__date__lte=cutoff
        )
        if not unread.first():
            return
        count = unread.count()
        if not count:
            return
        noun = "activity" if count == 1 else "activities"
        preview = Notification(
            notificationID="NTF_community_preview_" + str(uuid.uuid4()),
            referenceID=str(realm.entity_id),
            referenceStatus=True,
            toUserID=str(recipient),
            fromUserID=str(realm.entity_id),
            is_anonymous=False,
            type=ADMIN_ACTIVITY,
            content=Content(
                headline=PREVIEW_HEADLINE,
                details=f"{count} new {noun} in your community. Check them out.",
            ),
            date=DateInfo(date=cutoff),
            target=NotificationTarget(
                type="community_notifications", supportingID=str(realm.pk)
            ),
            redirects=_redirects(realm),
            isRead=False,
        )
        updates = {
            f"set__{field}": getattr(preview, field)
            for field in (
                "notificationID",
                "referenceID",
                "referenceStatus",
                "toUserID",
                "fromUserID",
                "is_anonymous",
                "type",
                "content",
                "date",
                "target",
                "redirects",
                "isRead",
            )
        }
        query = Notification.objects(id=slot_id)
        if previous_id:
            query = query.filter(notificationID=previous_id)
        else:
            query = query.filter(notificationID__exists=False)
        try:
            if not query.update_one(upsert=previous is None, **updates):
                continue
            break
        except NotUniqueError:
            # A competing writer won. Recount against its new logical revision.
            continue
    Notification.objects(
        type=ADMIN_ACTIVITY,
        toUserID=str(recipient),
        fromUserID=str(realm.entity_id),
        id__ne=slot_id,
    ).delete()
    _reconcile_previews(realm, recipient)
    unread_preview = Notification.objects(
        notificationID=preview.notificationID, isRead=False
    ).first()
    _publish_event(
        realm,
        recipient,
        "notifications" if unread_preview else "notifications_reload",
        personal=True,
    )
    _publish_event(realm, recipient, "community_admin_preview_changed", personal=True)


def notify_join_request(invite):
    """Write a join request and refresh the administrator previews immediately."""
    try:
        current = (
            Invite.objects.select_related("realm", "target_entity")
            .filter(
                pk=invite.pk,
                kind="request",
                status="pending",
                realm__type="community",
                realm__is_active=True,
            )
            .first()
        )
        if current is None or current.target_entity is None:
            return
        realm = current.realm
        notice = dict(
            referenceID=str(current.pk),
            toUserID=str(realm.entity_id),
            fromUserID=str(current.target_entity_id),
            is_anonymous=False,
            type=JOIN_REQUEST,
            referenceStatus=False,
            content_headline="Join request",
            content_details=(
                f"{get_entity_display_username(current.target_entity)} "
                f"wants to join {realm.name}."
            ),
            target_type="community_notifications",
            target_id=str(realm.pk),
            redirects=_redirects(realm),
        )
        record_admin_activity(realm, notice, actor_id=current.target_entity_id)
        if not Invite.objects.filter(pk=current.pk, status="pending").exists():
            settle_join_requests([str(current.pk)])
    except Exception:
        logger.exception("Failed to deliver community join-request notices")


def notify_member_joined(member_id):
    """After commit, record a successful community join once per membership."""
    try:
        member = (
            Member.objects.select_related("realm", "entity")
            .filter(pk=member_id, realm__type="community", realm__is_active=True)
            .first()
        )
        if member is None:
            return
        realm = member.realm
        notice = dict(
            referenceID=str(member.pk),
            toUserID=str(realm.entity_id),
            fromUserID=str(member.entity_id),
            is_anonymous=False,
            type=MEMBER_JOINED,
            content_headline="Member joined",
            content_details=f"{get_entity_name(member.entity)} joined {realm.name}.",
            target_type="community_notifications",
            target_id=str(realm.pk),
        )
        record_admin_activity(realm, notice, actor_id=member.entity_id)
    except Exception:
        logger.exception("Failed to record community member join")


def _reconcile_previews(realm, recipient):
    for preview in _summary(realm, recipient).filter(isRead=False):
        if not _admin_inbox(realm, recipient).filter(
            isRead=False, date__date__lte=preview.date.date
        ).first():
            Notification.objects(notificationID=preview.notificationID).update_one(
                set__isRead=True
            )


def settle_join_requests(reference_ids):
    if not reference_ids:
        return
    try:
        notices = Notification.objects(
            type=JOIN_REQUEST, referenceID__in=list(map(str, reference_ids))
        )
        ids = [row.notificationID for row in notices]
        notices.update(set__referenceStatus=True, set__isRead=True)
        Notification.objects(type=ADMIN_INBOX, referenceID__in=ids).update(
            set__referenceStatus=True, set__isRead=True
        )
        realms = {
            invite.realm_id: invite.realm
            for invite in Invite.objects.select_related("realm").filter(
                pk__in=reference_ids, realm__type="community"
            )
        }
        for realm in realms.values():
            managers = _managers(realm)
            for recipient in managers:
                _reconcile_previews(realm, recipient)
            _event(realm, managers)
            _preview_read_event(realm, managers)
    except Exception:
        logger.exception("Failed to settle community join-request notices")


def record_admin_activity(realm, notice, *, actor_id=None):
    """Store immediate inbox rows and refresh personal unread previews."""
    canonical_id = _notice_id(notice["referenceID"], notice["toUserID"], notice["type"])
    managers = [
        recipient for recipient in _managers(realm) if str(recipient) != str(actor_id)
    ]
    inserted = []
    for recipient in managers:
        # The existing isRead field gives each administrator independent read
        # state. Create these rows before exposing the shared inbox item.
        if _upsert_notice(**{
            **notice,
            "type": ADMIN_INBOX,
            "referenceID": canonical_id,
            "toUserID": str(recipient),
        }):
            inserted.append(recipient)
    _upsert_notice(**notice)
    _event(realm, managers)
    for recipient in inserted:
        try:
            _save_preview(realm, recipient)
        except Exception:
            logger.exception("Failed to save community preview for %s", recipient)


def _summary(realm, viewer):
    return Notification.objects(
        type=ADMIN_ACTIVITY,
        content__headline=PREVIEW_HEADLINE,
        fromUserID=str(realm.entity_id),
        toUserID=str(viewer.pk if hasattr(viewer, "pk") else viewer),
    )


def _preview_read_event(realm, recipients):
    for recipient in recipients:
        for event in ("notifications_reload", "community_admin_preview_changed"):
            _publish_event(realm, recipient, event, personal=True)


def acknowledge_activity(realm, viewer, revision, notification_ids=None):
    """Only acknowledge rows the client actually loaded, never newer arrivals."""
    if not notification_ids:
        return False
    # An older shared inbox item may not have a per-admin row yet. Persist its
    # read state here so replay cannot turn already-viewed activity unread.
    # Supplied IDs must belong to this community inbox.
    notices = Notification.objects(
        notificationID__in=notification_ids,
        toUserID=str(realm.entity_id),
        type__in=INBOX_TYPES,
    )
    changed = False
    for notice in notices:
        result = Notification.objects(
            notificationID=_notice_id(notice.notificationID, viewer.pk, ADMIN_INBOX)
        ).update_one(
            upsert=True,
            full_result=True,
            set__isRead=True,
            set_on_insert__type=ADMIN_INBOX,
            set_on_insert__referenceID=notice.notificationID,
            set_on_insert__toUserID=str(viewer.pk),
            set_on_insert__fromUserID=notice.fromUserID,
            set_on_insert__is_anonymous=notice.is_anonymous,
            set_on_insert__content=notice.content,
            set_on_insert__target=notice.target,
            set_on_insert__date=notice.date,
            set_on_insert__referenceStatus=notice.referenceStatus,
        )
        changed = changed or result.upserted_id is not None or result.modified_count > 0
    _reconcile_previews(realm, viewer.pk)
    if changed:
        _preview_read_event(realm, [viewer.pk])
    return bool(changed)


def _manager_realm(request, realm_id):
    realm = get_object_or_404(
        Realm, Q(pk=realm_id) | Q(slug=realm_id), type="community", is_active=True
    )
    if not Member.objects.filter(
        realm=realm, entity=request.entity, role__in=MANAGER_ROLES
    ).exists() or not has_permission(
        request.entity, Permission.REALM_REQUEST_APPROVE, realm=realm
    ):
        raise PermissionDenied("Only community managers can view these notifications")
    return realm


def _actor_queryset(model):
    return model.objects.select_related(
        "entity__users", "entity__realms", "entity__bots"
    )


def _project_notices(realm, notices):
    """Re-read authoritative activity before projecting identity or a destination."""
    references = [row.referenceID for row in notices]
    requests = {
        str(item.pk): item
        for item in Invite.objects.select_related(
            "target_entity__users", "target_entity__realms", "target_entity__bots"
        ).filter(pk__in=references, realm=realm, kind="request")
    }
    post_ids = [
        row.target.supportingID
        for row in notices
        if row.target and row.target.type == "post"
    ]
    scoped_ids = PostTag.objects.filter(
        post_id__in=post_ids, entity_id=realm.entity_id
    ).values_list("post_id", flat=True)
    posts = {
        str(item.pk): item
        for item in _actor_queryset(Post).filter(
            pk__in=scoped_ids, deleted_at=None, is_archived=False
        )
    }
    media_posts = {
        key: post
        for key, post in posts.items()
        if str(post.entity_id) == str(realm.entity_id)
        and post.from_system
        and (post.details or {}).get("community_media_update") is True
    }
    joined_actors = {
        str(entity.pk): entity
        for entity in Entity.objects.select_related("users", "realms", "bots").filter(
            pk__in=[row.fromUserID for row in notices if row.type == MEMBER_JOINED]
        )
    }
    activities = {
        "post_comment": {
            str(item.pk): item
            for item in _actor_queryset(Comment).filter(
                pk__in=references, post_id__in=media_posts, deleted_at=None
            )
        },
        "post_reaction": {
            str(item.pk): item
            for item in _actor_queryset(Reaction).filter(
                pk__in=references, post_id__in=media_posts
            )
        },
        "comment_reaction": {
            str(item.pk): item
            for item in _actor_queryset(CommentReaction)
            .select_related("comment")
            .filter(
                pk__in=references, comment__post_id__in=media_posts, comment__deleted_at=None
            )
        },
    }
    results = []
    for row in notices:
        request = (
            requests.get(str(row.referenceID)) if row.type == JOIN_REQUEST else None
        )
        activity = activities.get(row.type, {}).get(str(row.referenceID))
        new_post = posts.get(str(row.referenceID)) if row.type == POST_CREATED else None
        joined = row.type == MEMBER_JOINED and row.target and (
            row.target.type == "community_notifications"
            and str(row.target.supportingID) == str(realm.pk)
        )
        if request is None and activity is None and new_post is None and not joined:
            continue
        anonymous = False
        if joined:
            actor = joined_actors.get(str(row.fromUserID))
        elif new_post is not None:
            actor = new_post.entity
            anonymous = bool(new_post.is_anonymous)
        elif request is not None:
            actor = request.target_entity
        else:
            actor = activity.entity
            anonymous = bool(activity.is_anonymous)
        if anonymous:
            subject = "An anonymous member"
        else:
            subject = get_entity_name(actor) if actor is not None else "Someone"
        post_id = None
        if joined:
            description = f"{subject} joined the community."
        elif new_post is not None:
            post_id = str(new_post.pk)
            if row.target is None or str(row.target.supportingID) != post_id:
                continue
            description = f"{subject} posted in the community."
        elif request is not None:
            description = (
                f"{subject} wants to join the community." if request.status == "pending"
                else f"{subject}’s join request is {request.status}."
            )
        else:
            post_id = str(activity.comment.post_id if row.type == "comment_reaction" else activity.post_id)
            if row.target is None or str(row.target.supportingID) != post_id:
                continue
            verb = {"post_comment": "commented on", "post_reaction": "reacted to",
                    "comment_reaction": "reacted to a comment on"}[row.type]
            description = f"{subject} {verb} the community’s post."
        results.append(
            {
                "id": row.notificationID,
                "type": row.type,
                "description": description,
                "actor": None
                if anonymous or actor is None
                else EntitySerializer(actor).data,
                "is_anonymous": bool(anonymous),
                "created_at": row.date.date,
                "request_id": str(request.pk) if request is not None else None,
                "request_status": request.status if request is not None else None,
                "post_id": post_id,
                "anchor": row.target.anchor if post_id and row.target else None,
            }
        )
    return results


class CommunityNotificationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, realm_id):
        realm = _manager_realm(request, realm_id)
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            size = max(1, min(50, int(request.query_params.get("page_size", 20))))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Invalid notification pagination") from exc
        query = Notification.objects(
            toUserID=str(realm.entity_id),
            type__in=INBOX_TYPES,
        )
        total = query.count()
        rows = list(
            query.order_by("-date__date", "-notificationID")
            .skip((page - 1) * size)
            .limit(size)
        )
        url = request.build_absolute_uri()
        return Response(
            {
                "count": total,
                "next": replace_query_param(url, "page", page + 1)
                if page * size < total
                else None,
                "previous": replace_query_param(url, "page", page - 1)
                if page > 1
                else None,
                "activity_revision": 0,
                "results": _project_notices(realm, rows),
            }
        )

    def post(self, request, realm_id):
        realm = _manager_realm(request, realm_id)
        revision = request.data.get("activity_revision")
        if type(revision) is not int or revision < 0:
            raise ValidationError("Invalid activity revision")
        notification_ids = request.data.get("notification_ids", [])
        if (
            not isinstance(notification_ids, list)
            or len(notification_ids) > 1000
            or any(
                not isinstance(value, str) or not value or len(value) > 200
                for value in notification_ids
            )
        ):
            raise ValidationError("Invalid notification IDs")
        acknowledged = acknowledge_activity(
            realm, request.entity, revision, notification_ids
        )
        return Response({"status": True, "acknowledged": acknowledged})
