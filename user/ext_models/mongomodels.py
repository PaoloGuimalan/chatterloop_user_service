from mongoengine import (
    Document,
    StringField,
    ListField,
    BooleanField,
    EmbeddedDocument,
    EmbeddedDocumentField,
    IntField,
)


from mongoengine import (
    Document,
    StringField,
    ListField,
    BooleanField,
    EmbeddedDocument,
    EmbeddedDocumentField,
    IntField,
)


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
