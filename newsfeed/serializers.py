from rest_framework import serializers
from .models import (
    Post,
    PostTag,
    PostPrivacy,
    PostReference,
    MapView,
    Emoji,
    PreviewCount,
    Comment,
    ActivityCount,
    CountType,
    PostScore,
    PostSave,
)
from user.serializers import AccountPreviewSerializer
from community.models import Realm
from entity.services import build_entity_id, parse_entity_id, resolve_entity


class PostTagSerializer(serializers.ModelSerializer):
    user = AccountPreviewSerializer(read_only=True)

    class Meta:
        model = PostTag
        fields = "__all__"


class PostPrivacySerializer(serializers.ModelSerializer):
    class Meta:
        model = PostPrivacy
        fields = "__all__"


class PostReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostReference
        fields = "__all__"


class MapInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = MapView
        fields = "__all__"


class PreviewCountSerializer(serializers.ModelSerializer):
    class Meta:
        model = PreviewCount
        fields = ["count", "emoji"]


class CountTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CountType
        fields = "__all__"


class ActivityCountSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityCount
        fields = ["count_type", "count"]


class PostScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostScore
        fields = "__all__"


class RealmPreviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Realm
        fields = [
            "id",
            "realm_id",
            "name",
            "profile",
            "type",
            "is_verified",
            "slug",
        ]


def build_actor_payload(obj):
    """Unified ``actor`` representation, resolved through the entity's concrete
    account/realm link (use ``select_related('actor_entity__account',
    'actor_entity__realm')`` to avoid N+1). Falls back to the legacy ``user``.
    """
    entity = getattr(obj, "actor_entity", None)
    if entity is not None:
        if entity.entity_type == "realm" and entity.realm_id:
            return {
                "entity_id": entity.entity_id,
                "entity_type": "realm",
                "display": RealmPreviewSerializer(entity.realm).data,
            }
        if entity.account_id:
            return {
                "entity_id": entity.entity_id,
                "entity_type": "user",
                "display": AccountPreviewSerializer(entity.account).data,
            }

    account = getattr(obj, "user", None)
    if account is not None:
        return {
            "entity_id": build_entity_id("user", str(account.id)),
            "entity_type": "user",
            "display": AccountPreviewSerializer(account).data,
        }
    return None


def author_realm_payload(obj):
    """Back-compat ``author_realm``: the realm only when the actor is a realm
    (derived from actor_entity, which replaced the dropped author_realm FK)."""
    entity = getattr(obj, "actor_entity", None)
    if entity is not None and entity.entity_type == "realm" and entity.realm_id:
        return RealmPreviewSerializer(entity.realm).data
    return None


class PostSerializer(serializers.ModelSerializer):
    tagging = PostTagSerializer(many=True, read_only=True)
    privacy_users = PostPrivacySerializer(many=True, read_only=True)
    references = PostReferenceSerializer(many=True, read_only=True)
    map_info = MapInfoSerializer(read_only=True)
    preview = PreviewCountSerializer(read_only=True, many=True)
    user_reaction = serializers.CharField()
    user = AccountPreviewSerializer(read_only=True)
    author_realm = serializers.SerializerMethodField()
    actor = serializers.SerializerMethodField()
    # activity_counts = ActivityCountSerializer(read_only=True, many=True)
    score = PostScoreSerializer(read_only=True)
    is_saved = serializers.BooleanField(read_only=True)

    class Meta:
        model = Post
        fields = "__all__"

    def get_actor(self, obj):
        return build_actor_payload(obj)

    def get_author_realm(self, obj):
        return author_realm_payload(obj)


class EmojiSerializer(serializers.ModelSerializer):
    class Meta:
        model = Emoji
        fields = "__all__"


class CommentSerializer(serializers.ModelSerializer):
    user = AccountPreviewSerializer(read_only=True)
    actor = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = "__all__"

    def get_actor(self, obj):
        return build_actor_payload(obj)


class PostBasicSerializer(serializers.ModelSerializer):
    user = AccountPreviewSerializer(read_only=True)
    author_realm = serializers.SerializerMethodField()
    actor = serializers.SerializerMethodField()

    def get_author_realm(self, obj):
        return author_realm_payload(obj)

    class Meta:
        model = Post
        fields = "__all__"

    def get_actor(self, obj):
        return build_actor_payload(obj)


class PostSaveSerializer(serializers.ModelSerializer):
    post = PostBasicSerializer(read_only=True)

    class Meta:
        model = PostSave
        fields = "__all__"
