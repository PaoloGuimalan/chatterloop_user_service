from rest_framework import serializers
from .models import Entry, Attachment, MapView, Mood
from interests.models import Interest
from interests.services.affinity import bump_interest_affinity
from interests.services.interest_resolver import ensure_grant_override
from newsfeed.services.link_preview import extract_first_url_from_html, get_preview


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Interest
        fields = ["id", "name"]
        read_only_fields = ["id"]


class MoodSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mood
        fields = "__all__"


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = ["id", "url", "created_at", "file_type", "file_name"]
        read_only_fields = ["id", "created_at", "file_type"]


class MapViewSerializer(serializers.ModelSerializer):
    class Meta:
        model = MapView
        fields = [
            "map_view_id",
            "status",
            "is_stationary",
            "latitude",
            "longitude",
        ]
        read_only_fields = ["map_view_id"]


class EntrySerializer(serializers.ModelSerializer):
    # Write: list of tag names; Read: full tag objects
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50),
        write_only=True,
        required=False,
    )
    tag_objects = TagSerializer(source="tags", many=True, read_only=True)

    attachments = AttachmentSerializer(many=True, read_only=True)
    entry_map_info = MapViewSerializer(read_only=True)
    mood = MoodSerializer()
    link_preview = serializers.SerializerMethodField()

    def get_link_preview(self, obj):
        url = extract_first_url_from_html(obj.content or "")
        return get_preview(url) if url else None

    class Meta:
        model = Entry
        fields = [
            "id",
            "account",
            "title",
            "content",
            "entry_date",
            "mood",
            "is_private",
            "tags",  # write-only list of strings
            "tag_objects",  # read-only detailed tags
            "attachments",
            "entry_map_info",
            "link_preview",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def _handle_tags(self, entry, tag_names):
        if tag_names is None:
            return
        tags = []
        for name in tag_names:
            cleaned = name.strip()
            if not cleaned:
                continue
            tag, _ = Interest.objects.get_or_create_by_name(cleaned)
            tags.append(tag)
        entry.tags.set(tags)
        if tags:
            tag_ids = [tag.id for tag in tags]
            bump_interest_affinity(entry.account.entity_id, tag_ids, "DIARY_TAG", False)
            ensure_grant_override(entry.account.entity_id, tag_ids)

    def create(self, validated_data):
        tag_names = validated_data.pop("tags", [])
        entry = super().create(validated_data)
        self._handle_tags(entry, tag_names)
        return entry

    def update(self, instance, validated_data):
        tag_names = validated_data.pop("tags", None)
        entry = super().update(instance, validated_data)
        if tag_names is not None:
            self._handle_tags(entry, tag_names)
        return entry
