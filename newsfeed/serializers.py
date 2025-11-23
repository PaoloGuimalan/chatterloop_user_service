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
)
from user.serializers import AccountPreviewSerializer


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


class PostSerializer(serializers.ModelSerializer):
    tagging = PostTagSerializer(many=True, read_only=True)
    privacy_users = PostPrivacySerializer(many=True, read_only=True)
    references = PostReferenceSerializer(many=True, read_only=True)
    map_info = MapInfoSerializer(read_only=True)
    preview = PreviewCountSerializer(read_only=True, many=True)
    user_reaction = serializers.CharField()
    user = AccountPreviewSerializer(read_only=True)
    # activity_counts = ActivityCountSerializer(read_only=True, many=True)
    score = PostScoreSerializer(read_only=True)

    class Meta:
        model = Post
        fields = "__all__"


class EmojiSerializer(serializers.ModelSerializer):
    class Meta:
        model = Emoji
        fields = "__all__"


class CommentSerializer(serializers.ModelSerializer):
    user = AccountPreviewSerializer(read_only=True)

    class Meta:
        model = Comment
        fields = "__all__"
