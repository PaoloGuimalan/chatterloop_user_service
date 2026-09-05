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
    CommentPreviewCount,
    ActivityCount,
    CountType,
    PostScore,
    PostSave,
)
from user.serializers import AccountPreviewSerializer
from entity.serializers import EntitySerializer
from community.models import Realm
from .services.link_preview import extract_first_url, get_preview


class PostTagSerializer(serializers.ModelSerializer):
    entity = EntitySerializer(read_only=True)

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
    privacy_entity = PostPrivacySerializer(many=True, read_only=True)
    references = PostReferenceSerializer(many=True, read_only=True)
    map_info = MapInfoSerializer(read_only=True)
    preview = PreviewCountSerializer(read_only=True, many=True)
    entity_reaction = serializers.CharField()
    entity = EntitySerializer(read_only=True)
    # author_realm = RealmPreviewSerializer(read_only=True)
    # activity_counts = ActivityCountSerializer(read_only=True, many=True)
    score = PostScoreSerializer(read_only=True)
    is_saved = serializers.BooleanField(read_only=True)
    link_preview = serializers.SerializerMethodField()
    feed_reason = serializers.SerializerMethodField()

    def get_link_preview(self, obj):
        url = extract_first_url(obj.caption or "")
        return get_preview(url) if url else None

    def get_feed_reason(self, obj):
        """
        Why this post is in the viewer's feed, for the caption above the card
        ("@handle commented on this post").

        Comes from CONTEXT, not from the post: the reason is a property of the
        row that put the post in one person's feed, not of the post itself -
        the same post is in one feed because you follow its author and in
        another because a third party commented on it. Only NewsfeedView knows
        that, and only it passes `feed_reasons`.

        Null everywhere else, and null for the reasons the feed does not
        explain (see SILENT_FEED_REASONS) - the field is always present so a
        client can read it without checking which endpoint it came from.
        """
        reasons = self.context.get("feed_reasons")
        if not reasons:
            return None

        return reasons.get(str(obj.post_id))

    class Meta:
        model = Post
        fields = "__all__"


class EmojiSerializer(serializers.ModelSerializer):
    class Meta:
        model = Emoji
        fields = "__all__"


class CommentPreviewCountSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommentPreviewCount
        # Same shape as PreviewCountSerializer so the client can render a
        # comment's reaction row with the post's component unchanged.
        fields = ["count", "emoji"]


class CommentSerializer(serializers.ModelSerializer):
    entity = EntitySerializer(read_only=True)
    link_preview = serializers.SerializerMethodField()
    reply_count = serializers.SerializerMethodField()
    preview = CommentPreviewCountSerializer(read_only=True, many=True)
    entity_reaction = serializers.SerializerMethodField()

    def get_entity_reaction(self, obj):
        # Annotated by CommentsView for the viewer; absent for a guest (the
        # comments GET is AllowAny) and for any other consumer, where "no
        # reaction" is the right answer anyway.
        return getattr(obj, "entity_reaction", None)

    def get_link_preview(self, obj):
        url = extract_first_url(obj.text or "")
        return get_preview(url) if url else None

    def get_reply_count(self, obj):
        # Annotated by the view for the top-level list ("View 3 replies").
        # Read defensively: the replies list doesn't annotate it (a reply
        # never has children - threads are two levels deep, see
        # Comment.parent_comment), and neither does any other consumer, but
        # the key should still be present and 0 rather than missing.
        return getattr(obj, "reply_count", 0)

    class Meta:
        model = Comment
        fields = "__all__"


class PostBasicSerializer(serializers.ModelSerializer):
    entity = EntitySerializer(read_only=True)

    class Meta:
        model = Post
        fields = "__all__"


class PostSaveSerializer(serializers.ModelSerializer):
    post = PostBasicSerializer(read_only=True)

    class Meta:
        model = PostSave
        fields = "__all__"
