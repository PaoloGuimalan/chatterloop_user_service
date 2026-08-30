from mongoengine import (
    Document,
    StringField,
    ListField,
    BooleanField,
    EmbeddedDocument,
    EmbeddedDocumentField,
    IntField,
    DateTimeField,
    DictField,
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


class NotificationTarget(EmbeddedDocument):
    """WHERE the notification points, for the client.

    Deliberately separate from ``referenceID``, which is a BACKEND field: it
    holds whatever id the server-side action needs (a connection id to accept,
    a requester's entity id to approve), its meaning changes per notification
    type, and it is frequently not the thing the user wants to look at - a
    post_reaction stores the REACTION id, a post_comment the COMMENT id. Routing
    a client off that produced either no link or a confidently wrong one.
    """

    # "post" | "profile" | "conversation" | "realm" | "server" | ...
    type = StringField()
    # The id of that thing - the POST id for a comment notification, not the
    # comment's.
    supportingID = StringField()
    # Optional: what to scroll to once there. A comment notification opens the
    # post (supportingID) at that comment (anchor). Clients that do not
    # implement anchoring just open the target.
    anchor = StringField()


class Notification(Document):
    notificationID = StringField(required=True, unique=True)
    # BACKEND id - see NotificationTarget for why the client does not route
    # off this.
    referenceID = StringField(required=True)
    referenceStatus = BooleanField(default=False)
    toUserID = StringField(required=True)
    fromUserID = StringField(required=True)
    content = EmbeddedDocumentField(Content, required=True)
    date = EmbeddedDocumentField(DateInfo, required=True)
    type = StringField(required=True)
    isRead = BooleanField(default=False)
    # Optional: rows written before this existed have none, and the Node read
    # path falls back to what it can infer from `type` + `referenceID`.
    target = EmbeddedDocumentField(NotificationTarget)
    __v = IntField(db_field="__v")

    meta = {"collection": "notifications"}


class ModerationRecord(Document):
    """
    READ-ONLY view of moderation_service's `moderation` collection.

    Django never writes this - the service that produces a verdict owns it.
    Declared here so the moderation-detail endpoint can read a record without
    the verdict being duplicated into Postgres, where a re-analysis would leave
    two records of one judgement disagreeing with each other.

    Deliberately partial: only the fields that surface belong here. `strict:
    False` is what marks a document as context-only, and the mapping is loose
    (DictField) because the service owns that shape and adds to it - a strict
    schema here would break on the service's next field.
    """

    moderationID = StringField(required=True)
    targetID = StringField()
    foreignID = ListField(StringField())
    sourceType = StringField()
    contentType = StringField()
    entityID = StringField()

    text = StringField()
    transcription = StringField()
    caption = StringField()

    moderation = DictField()

    meta = {"collection": "moderation", "strict": False}


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
