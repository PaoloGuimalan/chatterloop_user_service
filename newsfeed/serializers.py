from rest_framework import serializers
from .models import Post, PostTag, PostPrivacy, PostReference, MapView, Emoji
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


class PostSerializer(serializers.ModelSerializer):
    tagging = PostTagSerializer(many=True, read_only=True)
    privacy_users = PostPrivacySerializer(many=True, read_only=True)
    references = PostReferenceSerializer(many=True, read_only=True)
    map_info = MapInfoSerializer(read_only=True)
    user = AccountPreviewSerializer(read_only=True)

    class Meta:
        model = Post
        fields = "__all__"


class EmojiSerializer(serializers.ModelSerializer):
    class Meta:
        model = Emoji
        fields = "__all__"
