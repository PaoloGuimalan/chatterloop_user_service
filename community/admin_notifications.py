"""Community management inbox and per-administrator bell projections."""

import logging
import uuid
from datetime import datetime

from django.db.models import Q
from django.shortcuts import get_object_or_404
from mongoengine import NotUniqueError
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.utils.urls import replace_query_param

from community.models import Invite, Member, Realm
from entity.permissions import Permission
from entity.serializers import EntitySerializer
from entity.services.permission_resolver import has_permission
from entity.utils import get_entity_display_username, get_entity_name
from newsfeed.models import Comment, CommentReaction, Post, Reaction
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
INBOX_TYPES = (JOIN_REQUEST, "post_comment", "post_reaction", "comment_reaction")
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


def _event(realm, recipients, *, personal_type=None):
    for recipient in recipients:
        for event in (
            ("community_notifications_changed", "notifications")
            if personal_type
            else ("community_notifications_changed",)
        ):
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
                            **({"type": personal_type} if personal_type else {}),
                        },
                    },
                    "dateTime": datetime.now().astimezone().isoformat(),
                },
            )


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
            "community_entity_id",
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
        set_on_insert__date=DateInfo(date=datetime.now().astimezone().isoformat()),
        set_on_insert__isRead=False,
    )
    try:
        result = Notification.objects(notificationID=notification_id).update_one(
            upsert=True, full_result=True, **updates
        )
        return result.upserted_id is not None
    except NotUniqueError:
        # A competing first delivery won the unique notificationID. Refresh
        # its safe projection, but do not increment the summary a second time.
        Notification.objects(notificationID=notification_id).update_one(**updates)
        return False


def notify_join_request(invite):
    """After commit, persist one shared inbox event and personal manager notices."""
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
        # Personal bell renderers supply the bold display name and strip the
        # standard @handle prefix. The inbox resolves its own full sentence.
        description = f"{get_entity_display_username(current.target_entity)} wants to join {realm.name}."
        recipients = _managers(realm)
        for recipient in {str(realm.entity_id), *map(str, recipients)}:
            if recipient == str(current.target_entity_id):
                continue
            _upsert_notice(
                referenceID=str(current.pk),
                toUserID=recipient,
                fromUserID=str(current.target_entity_id),
                community_entity_id=str(realm.entity_id),
                is_anonymous=False,
                type=JOIN_REQUEST,
                referenceStatus=False,
                content_headline="Join request",
                content_details=description,
                target_type="community_notifications",
                target_id=str(realm.pk),
                redirects=_redirects(realm),
            )
        if not Invite.objects.filter(pk=current.pk, status="pending").exists():
            settle_join_requests([str(current.pk)])
        _event(realm, recipients, personal_type=JOIN_REQUEST)
    except Exception:
        logger.exception("Failed to deliver community join-request notices")


def settle_join_requests(reference_ids):
    if not reference_ids:
        return
    try:
        Notification.objects(
            type=JOIN_REQUEST, referenceID__in=list(map(str, reference_ids))
        ).update(set__referenceStatus=True, set__isRead=True)
        realms = {
            invite.realm_id: invite.realm
            for invite in Invite.objects.select_related("realm").filter(
                pk__in=reference_ids, realm__type="community"
            )
        }
        for realm in realms.values():
            _event(realm, _managers(realm), personal_type=JOIN_REQUEST)
    except Exception:
        logger.exception("Failed to settle community join-request notices")


def record_admin_activity(realm, notice, *, actor_id=None):
    """Store the community event once and atomically increment unread summaries."""
    if not _upsert_notice(**notice):
        return
    managers = [
        recipient for recipient in _managers(realm) if str(recipient) != str(actor_id)
    ]
    for recipient in managers:
        # Personal activity counts are independent. Reading an existing bell
        # resets the next increment to one; unread arrivals increment in place.
        fields = {
            "referenceID": str(realm.entity_id),
            "toUserID": str(recipient),
            "fromUserID": str(realm.entity_id),
            "community_entity_id": str(realm.entity_id),
            "is_anonymous": False,
            "type": ADMIN_ACTIVITY,
            "referenceStatus": True,
            "content": {
                "headline": "Community activity",
                "details": f"New activities in {realm.name}. Check them out.",
            },
            "target": {
                "type": "community_notifications",
                "supportingID": str(realm.pk),
            },
            "redirects": _redirects(realm),
            "date": {"date": datetime.now().astimezone().isoformat()},
        }
        pipeline = [
            {
                "$set": {
                    **{key: {"$literal": value} for key, value in fields.items()},
                    "activity_count": {
                        "$cond": [
                            {"$eq": ["$isRead", True]},
                            1,
                            {"$add": [{"$ifNull": ["$activity_count", 0]}, 1]},
                        ]
                    },
                    "activity_revision": {
                        "$add": [{"$ifNull": ["$activity_revision", 0]}, 1]
                    },
                    "isRead": False,
                }
            }
        ]
        Notification.objects(
            notificationID=_notice_id(realm.entity_id, recipient, ADMIN_ACTIVITY)
        ).update_one(upsert=True, __raw__=pipeline)
    _event(realm, managers, personal_type=ADMIN_ACTIVITY)


def _summary(realm, viewer):
    return Notification.objects(
        type=ADMIN_ACTIVITY,
        community_entity_id=str(realm.entity_id),
        toUserID=str(viewer.pk),
    )


def acknowledge_activity(realm, viewer, revision):
    """Compare-and-set: an arrival after the read snapshot remains unread."""
    return bool(
        _summary(realm, viewer)
        .filter(activity_revision=revision)
        .update(set__activity_count=0, set__isRead=True)
    )


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
    posts = {
        str(item.pk): item
        for item in Post.objects.filter(
            pk__in=post_ids,
            entity_id=realm.entity_id,
            from_system=True,
            details__community_media_update=True,
            deleted_at=None,
            is_archived=False,
        )
    }
    activities = {
        "post_comment": {
            str(item.pk): item
            for item in _actor_queryset(Comment).filter(
                pk__in=references, post_id__in=posts, deleted_at=None
            )
        },
        "post_reaction": {
            str(item.pk): item
            for item in _actor_queryset(Reaction).filter(
                pk__in=references, post_id__in=posts
            )
        },
        "comment_reaction": {
            str(item.pk): item
            for item in _actor_queryset(CommentReaction)
            .select_related("comment")
            .filter(
                pk__in=references, comment__post_id__in=posts, comment__deleted_at=None
            )
        },
    }
    results = []
    for row in notices:
        request = (
            requests.get(str(row.referenceID)) if row.type == JOIN_REQUEST else None
        )
        activity = activities.get(row.type, {}).get(str(row.referenceID))
        if request is None and activity is None:
            continue
        actor = request.target_entity if request is not None else activity.entity
        anonymous = request is None and activity.is_anonymous
        subject = (
            "An anonymous member"
            if anonymous
            else get_entity_name(actor)
            if actor is not None
            else "Someone"
        )
        post_id = None
        if request is not None:
            description = (
                f"{subject} wants to join {realm.name}."
                if request.status == "pending"
                else f"{subject}’s join request is {request.status}."
            )
        else:
            post_id = str(
                activity.comment.post_id
                if row.type == "comment_reaction"
                else activity.post_id
            )
            if row.target is None or str(row.target.supportingID) != post_id:
                continue
            verb = {
                "post_comment": "commented on",
                "post_reaction": "reacted to",
                "comment_reaction": "reacted to a comment on",
            }[row.type]
            description = f"{subject} {verb} {realm.name}’s post."
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
        summary = _summary(realm, request.entity).first()
        revision = summary.activity_revision if summary else 0
        query = Notification.objects(
            toUserID=str(realm.entity_id),
            community_entity_id=str(realm.entity_id),
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
                "activity_revision": revision,
                "results": _project_notices(realm, rows),
            }
        )

    def post(self, request, realm_id):
        realm = _manager_realm(request, realm_id)
        revision = request.data.get("activity_revision")
        if type(revision) is not int or revision < 0:
            raise ValidationError("Invalid activity revision")
        acknowledged = acknowledge_activity(realm, request.entity, revision)
        return Response({"status": True, "acknowledged": acknowledged})
