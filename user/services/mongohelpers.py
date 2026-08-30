from ..ext_models.mongomodels import (
    Notification,
    NotificationTarget,
    DateInfo,
    Content,
)
from ..utils.generators import generate_random_digit
from datetime import datetime
from ..ext_models.mongomodels import Session
import uuid


# Audience selectors, mirroring server/routes/users/index.js. A toUserID that
# is one of these addresses MANY recipients; anything else is one entity.
SYSTEM_AUDIENCE_ALL = "all"
SYSTEM_AUDIENCE_PREFIXES = ("region:", "group:")


def _is_audience_selector(to_user_id) -> bool:
    tag = str(to_user_id or "")
    return tag == SYSTEM_AUDIENCE_ALL or tag.startswith(SYSTEM_AUDIENCE_PREFIXES)


class NotificationService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(NotificationService, cls).__new__(cls)
        return cls._instance

    def exists(self, notification_id):
        return Notification.objects(notificationID=notification_id).first() is not None

    def add_notification(
        self,
        referenceID,
        referenceStatus,
        toUserID,
        fromUserID,
        content_headline,
        content_details,
        type,
        isRead=True,
        target_type=None,
        target_id=None,
        target_anchor=None,
    ):
        """
        ``target_*`` describe where the CLIENT should go when the row is tapped,
        and are independent of ``referenceID`` (which is the backend's id for
        the action). Optional and keyword-only in practice: a call site that
        omits them writes no target, and the Node read path falls back to what
        it can infer from the notification type.
        """
        notification_id = f"NTF_{generate_random_digit(20)}"

        while self.exists(notification_id):
            notification_id = f"NTF_{generate_random_digit(20)}"

        # now = datetime.now()
        # date_str = now.strftime("%m/%d/%Y")
        # time_str = now.strftime("%I:%M:%S %p").lower()  # e.g. 8:50:31 pm

        new_now = datetime.now().astimezone()

        content = Content(headline=content_headline, details=content_details)
        date = DateInfo(date=str(new_now), time=None)

        # Only written when a call site actually supplied one - an empty
        # embedded document would be indistinguishable from "no target" to the
        # reader, and it treats absent as "infer from type".
        target = (
            NotificationTarget(
                type=target_type,
                supportingID=str(target_id) if target_id is not None else None,
                anchor=str(target_anchor) if target_anchor is not None else None,
            )
            if target_type and target_id is not None
            else None
        )

        # Read state is DERIVED, not taken from the caller.
        #
        # A notice addressed to one entity is personal, and a personal notice
        # starts unread - an `isRead=True` on one means the recipient is never
        # told about something that concerns only them. That is a silent
        # failure, and this function's default used to be exactly it: `isRead`
        # defaulted to True, and only the fact that every existing call site
        # passes False explicitly kept it from happening.
        #
        # Broadcasts are the exception. A notice addressed to MANY people is
        # one shared document that cannot carry per-viewer read state, so it is
        # stored already-read: it still appears in everyone's System section,
        # it just never badges and can never strand an unread count no read
        # call could clear. Mirrors resolveNotificationIsRead in
        # server/routes/users/index.js - the two must agree, because either can
        # write a notification.
        resolved_is_read = _is_audience_selector(toUserID)

        notif = Notification(
            notificationID=notification_id,
            referenceID=referenceID,
            referenceStatus=referenceStatus,
            toUserID=toUserID,
            fromUserID=fromUserID,
            content=content,
            date=date,
            type=type,
            isRead=resolved_is_read,
            target=target,
        )
        notif.save()
        return notif

    def update_reference_status(self, connection_id, new_status):
        result = Notification.objects(referenceID=connection_id).update(
            set__referenceStatus=new_status, multi=True
        )
        return result > 0

    def update_reference_status_by_type(
        self, reference_id, notif_type, new_status, to_user_id=None
    ):
        """
        Same as update_reference_status, but scoped to ONE notification type
        and optionally to ONE recipient.

        Needed where referenceID is not globally unique. A follow request
        carries the requester's ENTITY id as its referenceID (there is no
        connection row to point at), and other types key on an entity id too,
        so the unscoped version would settle unrelated notifications from the
        same person.

        `to_user_id` narrows it further to the answering recipient: one
        requester can have open requests against several profiles at once,
        and approving on one of them must not clear the buttons on the
        others.
        """
        query = {"referenceID": reference_id, "type": notif_type}
        if to_user_id:
            query["toUserID"] = str(to_user_id)

        result = Notification.objects(**query).update(
            set__referenceStatus=new_status, multi=True
        )
        return result > 0

    def update_content(self, reaction_id, new_content):
        result = Notification.objects(referenceID=reaction_id).update(
            set__content__details=new_content, multi=True
        )
        return result > 0

    def delete_notification_by_reference_id(self, reaction_id):
        result = Notification.objects(referenceID=reaction_id).delete()
        return result > 0

    def delete_notifications_by_reference_ids(self, reference_ids):
        """
        Bulk delete_notification_by_reference_id, returning the entity ids that
        had been notified.

        Deleting a comment takes its whole reply thread with it, and each of
        those comments is its own referenceID, so the single-id form would be
        one Mongo round-trip per reply.

        The recipients come back because a notification vanishing from the
        database is only half the job: whoever is looking at their list right
        now still has it on screen until they are told to reload. Read before
        the delete, since afterwards there is nothing left to read them from.
        """
        ids = [str(reference_id) for reference_id in reference_ids if reference_id]
        if not ids:
            return []

        recipients = [
            recipient
            for recipient in Notification.objects(referenceID__in=ids).distinct(
                "toUserID"
            )
            if recipient
        ]
        Notification.objects(referenceID__in=ids).delete()
        return recipients


class SessionService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SessionService, cls).__new__(cls)
        return cls._instance

    def exists(self, device_token, entity_id):
        """Check if a session exists by its deviceToken."""
        return (
            Session._get_collection().find_one(
                {"deviceToken": device_token, "entityID": str(entity_id)}
            )
            is not None
        )

    def add_session(self, request, entity_id, device_token):
        """Extracts all client parameters and user metadata straight from the Django request."""
        current_time = datetime.now().astimezone()

        # Extract values directly from the request object
        entity_id = str(entity_id)
        session_id = str(uuid.uuid4())
        user_agent = request.META.get("HTTP_USER_AGENT", "Unknown")

        # Get client IP address
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        ip_address = (
            x_forwarded_for.split(",")[0].strip()
            if x_forwarded_for
            else request.META.get("REMOTE_ADDR", "Unknown")
        )

        # Parse OS, Browser, and Device Type from User Agent string
        ua_lower = user_agent.lower()

        if "windows" in ua_lower:
            os_type = "Windows"
        elif "macintosh" in ua_lower or "mac os" in ua_lower:
            os_type = "MacOS"
        elif "iphone" in ua_lower or "ipad" in ua_lower:
            os_type = "iOS"
        elif "android" in ua_lower:
            os_type = "Android"
        elif "linux" in ua_lower:
            os_type = "Linux"
        else:
            os_type = "Unknown"

        if "chrome" in ua_lower and "safari" in ua_lower and "edge" not in ua_lower:
            browser_type = "Chrome"
        elif "safari" in ua_lower and "chrome" not in ua_lower:
            browser_type = "Safari"
        elif "firefox" in ua_lower:
            browser_type = "Firefox"
        elif "edge" in ua_lower or "edg" in ua_lower:
            browser_type = "Edge"
        else:
            browser_type = "Unknown"

        if any(mobile in ua_lower for mobile in ["iphone", "android", "mobile"]):
            device_type = "mobile"
        elif "ipad" in ua_lower or "tablet" in ua_lower:
            device_type = "tablet"
        else:
            device_type = "desktop"

        # Build and save the document
        session = Session(
            sessionID=session_id,
            entityID=entity_id,
            userAgent=user_agent,
            deviceType=device_type,
            deviceToken=device_token,
            status=False,  # Automatically set to False
            browser=browser_type,
            os=os_type,
            ip=ip_address,
            lastSeen=current_time,
        )
        session.save()
        return session

    def clear_fcm_token(self, device_token, entity_id):
        Session.objects(
            deviceToken=device_token,
            entityID=str(entity_id),
        ).update(set__fcmToken=None)

    def update_fcm_token(self, device_token, entity_id, fcm_token):
        """Upsert this device's FCM push token onto its session row - but only
        write when it changed. Runs on every authenticated request, so the
        fcmToken__ne filter makes it a no-op when the token is already current,
        while STILL matching rows whose fcmToken is missing/stale (so it also
        backfills already-logged-in devices on their next request). Scoped to
        (deviceToken, entityID) like exists(), so it never clobbers a different
        account's session on a shared device."""
        if not fcm_token:
            return
        Session.objects(
            deviceToken=device_token,
            entityID=str(entity_id),
            fcmToken__ne=fcm_token,
        ).update(set__fcmToken=fcm_token)

    def list_for_entity(self, entity_id):
        """Sessions for this exact entity only - deliberately excludes any
        page/realm session rows the same physical device may also hold from
        having switched entities, since the device list is account-level."""
        return Session.objects(entityID=str(entity_id)).order_by("-lastSeen")

    def revoke_device(self, device_token, allowed_entity_ids):
        """Deletes every session row for this deviceToken, but only across
        the given entity ids. deviceToken is generated once per browser/
        install, not per-account, so a blanket delete-by-deviceToken could
        reach a different account's session that happens to share the same
        physical device/browser - allowed_entity_ids must be this account's
        personal entity plus every page/realm it can switch into."""
        allowed = [str(entity_id) for entity_id in allowed_entity_ids]
        return Session.objects(deviceToken=device_token, entityID__in=allowed).delete()
