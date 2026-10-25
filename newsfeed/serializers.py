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
from community.posts import (
    community_for_post,
    community_metadata,
    viewer_reaction_identity,
)
from community.post_policy import project_contribution


class CommunityContributionMixin:
    def to_representation(self, instance):
        data = super().to_representation(instance)
        viewer = getattr(self.context.get("request"), "entity", None)
        post = instance if isinstance(instance, Post) else instance.post
        community = community_for_post(post)
        if community is None and not instance.is_anonymous:
            data.pop("is_anonymous", None)
            return data
        data = project_contribution(
            data,
            is_anonymous=instance.is_anonymous,
            owner_id=instance.entity_id,
            viewer_id=getattr(viewer, "pk", None),
        )
        if community is not None:
            request = self.context.get("request")
            reaction_viewer = (
                viewer
                if getattr(getattr(request, "user", None), "is_authenticated", False)
                else None
            )
            data["entity_reaction_is_anonymous"] = viewer_reaction_identity(
                instance, reaction_viewer
            )
        # A comment fanout row can predate the anonymity setting. Community
        # cards rely on destination metadata rather than social actor captions.
        if isinstance(instance, Post) and community is not None:
            data["feed_reason"] = None
            data["community"] = community_metadata(community, viewer)
        return data


class PostTagSerializer(serializers.ModelSerializer):
    entity = EntitySerializer(read_only=True)

    class Meta:
        model = PostTag
        fields = "__all__"


class PostPrivacySerializer(serializers.ModelSerializer):
    class Meta:
        model = PostPrivacy
        fields = "__all__"


class OrderedPostReferences(serializers.ListSerializer):
    def to_representation(self, data):
        # Stable append order for every read path, including prefetch results.
        items = data.all() if hasattr(data, "all") else data
        ordered = sorted(items, key=lambda item: (item.date_posted, item.reference_id))
        return super().to_representation(ordered)


class PostReferenceSerializer(serializers.ModelSerializer):
    is_edited = serializers.BooleanField(read_only=True)

    class Meta:
        model = PostReference
        fields = "__all__"

        read_only_fields = ["is_edited"]
        list_serializer_class = OrderedPostReferences


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


class PostSerializer(CommunityContributionMixin, serializers.ModelSerializer):
    tagging = PostTagSerializer(many=True, read_only=True)
    privacy_entity = PostPrivacySerializer(many=True, read_only=True)
    references = PostReferenceSerializer(many=True, read_only=True)
    map_info = MapInfoSerializer(read_only=True)
    preview = PreviewCountSerializer(read_only=True, many=True)
    entity_reaction = serializers.CharField()
    entity = EntitySerializer(read_only=True)
    score = PostScoreSerializer(read_only=True)
    is_saved = serializers.BooleanField(read_only=True)
    link_preview = serializers.SerializerMethodField()
    feed_reason = serializers.SerializerMethodField()
    is_edited = serializers.BooleanField(read_only=True)

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

        read_only_fields = ["is_edited"]


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


class CommentSerializer(CommunityContributionMixin, serializers.ModelSerializer):
    entity = EntitySerializer(read_only=True)
    link_preview = serializers.SerializerMethodField()
    reply_count = serializers.SerializerMethodField()
    preview = CommentPreviewCountSerializer(read_only=True, many=True)
    entity_reaction = serializers.SerializerMethodField()
    is_edited = serializers.BooleanField(read_only=True)

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

        read_only_fields = ["is_edited"]


class PostBasicSerializer(CommunityContributionMixin, serializers.ModelSerializer):
    entity = EntitySerializer(read_only=True)
    is_edited = serializers.BooleanField(read_only=True)

    class Meta:
        model = Post
        fields = "__all__"

        read_only_fields = ["is_edited"]


class PostSaveSerializer(serializers.ModelSerializer):
    post = PostBasicSerializer(read_only=True)

    class Meta:
        model = PostSave
        fields = "__all__"
