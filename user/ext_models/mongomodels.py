from mongoengine import (
    Document,
    StringField,
    ListField,
    BooleanField,
    EmbeddedDocument,
    EmbeddedDocumentField,
    IntField,
    DateTimeField,
)

from datetime import datetime


class MessageDate(EmbeddedDocument):
    date = StringField(required=True)
    time = StringField(required=True)


class Reaction(EmbeddedDocument):
    userID = StringField(required=True)
    activeSkinTone = StringField()
    emoji = StringField()
    imageUrl = StringField()
    isCustom = BooleanField(default=False)
    names = ListField(StringField())
    unified = StringField()
    unifiedWithoutSkinTone = StringField()


class Message(Document):
    meta = {"collection": "messages"}

    messageID = StringField(required=True, unique=True)
    conversationID = StringField(required=True)
    pendingID = StringField()
    sender = StringField(required=True)
    receivers = ListField(StringField())
    seeners = ListField(StringField())
    content = StringField()
    messageDate = EmbeddedDocumentField(MessageDate)
    isReply = BooleanField(default=False)
    replyingTo = StringField(blank=True)
    reactions = ListField(EmbeddedDocumentField(Reaction))
    isDeleted = BooleanField(default=False)
    messageType = StringField()
    conversationType = StringField()
    # Do NOT add __v field as it causes errors in MongoEngine
    __v = IntField(db_field="__v")


class ChatHistory(Document):
    meta = {"collection": "chat_history"}

    conversationID = StringField(required=True)
    entityID = StringField(required=True)
    cleared_at = DateTimeField(default=None)
    isArchived = BooleanField(default=False)
    isRestricted = BooleanField(default=False)


class LastMessage(EmbeddedDocument):
    messageID = StringField(default=None)
    sender = StringField(default=None)
    text = StringField(default="")
    messageDate = DateTimeField(default=datetime.utcnow)
    seeners = ListField(StringField())
    messageType = StringField(default="text")  # text, image, video, file, notif
    isDeleted = BooleanField(default=False)


class Conversation(Document):
    meta = {
        "collection": "conversations",
    }

    conversationID = StringField(required=True, unique=True)
    conversationType = StringField(default="single")
    participant_ids = ListField(StringField())
    last_message = EmbeddedDocumentField(LastMessage)
    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)


# Notifications Models

from mongoengine import (
    Document,
    StringField,
    BooleanField,
    EmbeddedDocument,
    EmbeddedDocumentField,
    DateTimeField,
    IntField,
)


class Content(EmbeddedDocument):
    headline = StringField(required=True)
    details = StringField(required=True)


class DateInfo(EmbeddedDocument):
    date = StringField(required=True)
    time = StringField()


class Notification(Document):
    notificationID = StringField(required=True, unique=True)
    referenceID = StringField(required=True)
    referenceStatus = BooleanField(default=False)
    toUserID = StringField(required=True)
    fromUserID = StringField(required=True)
    content = EmbeddedDocumentField(Content, required=True)
    date = EmbeddedDocumentField(DateInfo, required=True)
    type = StringField(required=True)
    isRead = BooleanField(default=False)
    __v = IntField(db_field="__v")

    meta = {"collection": "notifications"}


# END: Notifications Models


class Session(Document):
    meta = {"collection": "sessions"}

    sessionID = StringField(required=True)
    entityID = StringField(required=True)
    userAgent = StringField(required=True)
    deviceType = StringField(required=True)
    deviceToken = StringField(required=True)

    fcmToken = StringField(default=None)

    status = BooleanField(default=None)

    browser = StringField(default=None)
    os = StringField(default=None)
    ip = StringField(default=None)

    lastSeen = DateTimeField(default=None)

    __v = IntField(db_field="__v")
