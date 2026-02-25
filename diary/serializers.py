from rest_framework import serializers
from .models import Tag, Entry, Attachment, MapView, Mood


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
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
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def _handle_tags(self, entry, tag_names):
        if tag_names is None:
            return
        entry.tags.clear()
        for name in tag_names:
            cleaned = name.strip()
            if not cleaned:
                continue
            tag, _ = Tag.objects.get_or_create(name=cleaned)
            entry.tags.add(tag)

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
