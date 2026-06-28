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
from user.models import Account
from community.models import Realm


def serialize_user_entity_preview(entity):
    if not entity:
        return None
    if entity.entity_type != "user" or entity.source_type != "user.account":
        return {
            "id": entity.id,
            "username": entity.entity_id,
            "first_name": "",
            "middle_name": "",
            "last_name": "",
            "profile": "none",
            "gender": None,
            "is_badged": False,
        }

    account = Account.objects.filter(id=entity.source_id).first()
    if not account:
        return {
            "id": entity.source_id,
            "username": "",
            "first_name": "",
            "middle_name": "",
            "last_name": "",
            "profile": "none",
            "gender": None,
            "is_badged": False,
        }

    return {
        "id": account.id,
        "username": account.username,
        "first_name": account.first_name,
        "middle_name": account.middle_name,
        "last_name": account.last_name,
        "profile": account.profile,
        "gender": account.gender,
        "is_badged": account.is_badged,
    }


class PostTagSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    def get_user(self, obj):
        return serialize_user_entity_preview(obj.user)

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


class PostSerializer(serializers.ModelSerializer):
    tagging = PostTagSerializer(many=True, read_only=True)
    privacy_users = PostPrivacySerializer(many=True, read_only=True)
    references = PostReferenceSerializer(many=True, read_only=True)
    map_info = MapInfoSerializer(read_only=True)
    preview = PreviewCountSerializer(read_only=True, many=True)
    user_reaction = serializers.CharField()
    user = serializers.SerializerMethodField()
    author_realm = RealmPreviewSerializer(read_only=True)
    # activity_counts = ActivityCountSerializer(read_only=True, many=True)
    score = PostScoreSerializer(read_only=True)
    is_saved = serializers.BooleanField(read_only=True)

    def get_user(self, obj):
        return serialize_user_entity_preview(obj.user)

    class Meta:
        model = Post
        fields = "__all__"


class EmojiSerializer(serializers.ModelSerializer):
    class Meta:
        model = Emoji
        fields = "__all__"


class CommentSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    def get_user(self, obj):
        return serialize_user_entity_preview(obj.user)

    class Meta:
        model = Comment
        fields = "__all__"


class PostBasicSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    author_realm = RealmPreviewSerializer(read_only=True)

    def get_user(self, obj):
        return serialize_user_entity_preview(obj.user)

    class Meta:
        model = Post
        fields = "__all__"


class PostSaveSerializer(serializers.ModelSerializer):
    post = PostBasicSerializer(read_only=True)

    class Meta:
        model = PostSave
        fields = "__all__"
