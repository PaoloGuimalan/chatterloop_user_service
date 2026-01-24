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

        # now = datetime.now()
        # date_str = now.strftime("%m/%d/%Y")
        # time_str = now.strftime("%I:%M:%S %p").lower()  # e.g. 8:50:31 pm

        new_now = datetime.now().astimezone()

        content = Content(headline=content_headline, details=content_details)
        date = DateInfo(date=new_now, time=None)

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
