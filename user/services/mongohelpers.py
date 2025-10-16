from ..ext_models.mongomodels import Notification, DateInfo, Content
from ..utils.generators import generate_random_digit
from datetime import datetime


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

        now = datetime.now()
        date_str = now.strftime("%m/%d/%Y")
        time_str = now.strftime("%I:%M:%S %p").lower()  # e.g. 8:50:31 pm

        content = Content(headline=content_headline, details=content_details)
        date = DateInfo(date=date_str, time=time_str)

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
        notif = Notification.objects(referenceID=connection_id).first()
        if notif:
            notif.referenceStatus = new_status
            notif.save()
            return True  # update succeeded
        return False
