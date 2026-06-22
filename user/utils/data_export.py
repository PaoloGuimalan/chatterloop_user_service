from django.db.models import Q

from ..models import Connection, UserConsent
from ..ext_models.mongomodels import Message, Notification, Session


def export_account_data(account):
    from community.models import Realm, Member, RealmFollow, Invite
    from diary.models import Entry
    from newsfeed.models import Post, Comment, PostSave

    account_id = str(account.id)

    return {
        "profile": {
            "id": account.id,
            "username": account.username,
            "first_name": account.first_name,
            "middle_name": account.middle_name,
            "last_name": account.last_name,
            "birthdate": account.birthdate,
            "gender": account.gender,
            "email": account.email,
            "date_created": account.date_created,
            "is_active": account.is_active,
            "is_verified": account.is_verified,
            "is_badged": account.is_badged,
        },
        "connections": list(
            Connection.objects.filter(
                Q(action_by=account) | Q(involved_user=account)
            ).values(
                "id",
                "connection_id",
                "action_by_id",
                "involved_user_id",
                "nickname",
                "status",
                "action_date",
                "type",
            )
        ),
        "consents": list(
            UserConsent.objects.filter(user=account).values(
                "document_type", "version", "accepted_at", "ip_address", "user_agent"
            )
        ),
        "realms_created": list(
            Realm.objects.filter(created_by=account).values(
                "realm_id", "name", "type", "is_private", "created_at"
            )
        ),
        "realm_memberships": list(
            Member.objects.filter(account=account).values(
                "realm__realm_id", "realm__name", "role", "nickname", "date_joined"
            )
        ),
        "realm_follows": list(
            RealmFollow.objects.filter(follower=account).values(
                "realm__realm_id", "realm__name", "created_at"
            )
        ),
        "realm_invites_sent": list(
            Invite.objects.filter(created_by=account).values(
                "realm__realm_id", "target_email", "kind", "status", "created_at"
            )
        ),
        "diary_entries": list(
            Entry.objects.filter(account=account).values(
                "id",
                "title",
                "content",
                "entry_date",
                "is_private",
                "created_at",
                "updated_at",
            )
        ),
        "posts": list(
            Post.objects.filter(user=account).values(
                "post_id",
                "caption",
                "content_type",
                "privacy_status",
                "is_archived",
                "date_posted",
                "deleted_at",
            )
        ),
        "comments": list(
            Comment.objects.filter(user=account).values(
                "comment_id", "post_id", "text", "created_at", "deleted_at"
            )
        ),
        "post_saves": list(
            PostSave.objects.filter(user=account).values("post_id", "saved_at")
        ),
        "messages_sent": [
            {
                "messageID": doc.get("messageID"),
                "conversationID": doc.get("conversationID"),
                "content": doc.get("content"),
                "messageDate": doc.get("messageDate"),
                "isDeleted": doc.get("isDeleted", False),
            }
            for doc in Message._get_collection().find({"sender": account_id})
        ],
        "notifications": [
            {
                "notificationID": doc.get("notificationID"),
                "fromUserID": doc.get("fromUserID"),
                "type": doc.get("type"),
                "headline": (doc.get("content") or {}).get("headline"),
                "details": (doc.get("content") or {}).get("details"),
                "date": doc.get("date"),
                "isRead": doc.get("isRead", False),
            }
            for doc in Notification._get_collection().find({"toUserID": account_id})
        ],
        "sessions": [
            {
                "deviceType": doc.get("deviceType"),
                "browser": doc.get("browser"),
                "os": doc.get("os"),
                "lastSeen": doc.get("lastSeen"),
            }
            for doc in Session._get_collection().find({"userID": account_id})
        ],
    }
