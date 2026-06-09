from ..ext_models.mongomodels import Notification, DateInfo, Content
from ..utils.generators import generate_random_digit
from datetime import datetime
from ..ext_models.mongomodels import Session
import uuid


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
    ):
        notification_id = f"NTF_{generate_random_digit(20)}"

        while self.exists(notification_id):
            notification_id = f"NTF_{generate_random_digit(20)}"

        # now = datetime.now()
        # date_str = now.strftime("%m/%d/%Y")
        # time_str = now.strftime("%I:%M:%S %p").lower()  # e.g. 8:50:31 pm

        new_now = datetime.now().astimezone()

        content = Content(headline=content_headline, details=content_details)
        date = DateInfo(date=str(new_now), time=None)

        notif = Notification(
            notificationID=notification_id,
            referenceID=referenceID,
            referenceStatus=referenceStatus,
            toUserID=toUserID,
            fromUserID=fromUserID,
            content=content,
            date=date,
            type=type,
            isRead=isRead,
        )
        notif.save()
        return notif

    def update_reference_status(self, connection_id, new_status):
        result = Notification.objects(referenceID=connection_id).update(
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


class SessionService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SessionService, cls).__new__(cls)
        return cls._instance

    def exists(self, device_token, user_id):
        """Check if a session exists by its deviceToken."""
        return (
            Session.objects(deviceToken=device_token, userID=str(user_id)).first()
            is not None
        )

    def add_session(self, request, userID, device_token):
        """Extracts all client parameters and user metadata straight from the Django request."""
        current_time = datetime.now().astimezone()

        # Extract values directly from the request object
        user_id = str(userID)
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
            userID=user_id,
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
