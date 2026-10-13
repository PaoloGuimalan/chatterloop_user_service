from collections.abc import Mapping
from rest_framework import serializers
from .models import Entry, Attachment
from .services.content import normalize_note_content


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({key: "Unknown field" for key in sorted(unknown)})
        return super().to_internal_value(data)


class StrictVersionField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail("invalid", input=data)
        return data


class AttachmentWriteSerializer(StrictSerializer):
    id = serializers.UUIDField(required=False)
    url = serializers.CharField(required=False)
    # Accept Diary's payload shape, but resolve authoritative metadata server-side.
    file_id = serializers.CharField(max_length=150, required=False, allow_null=True)
    file_name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    file_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate(self, attrs):
        if "id" in attrs:
            if len(attrs) != 1:
                raise serializers.ValidationError("Use id alone to retain an attachment")
            return attrs
        if "url" not in attrs:
            raise serializers.ValidationError("Send the URL returned by the completed upload")
        return {"url": attrs["url"]}


class VersionSerializer(StrictSerializer):
    version = StrictVersionField(min_value=1)


class NoteWriteSerializer(StrictSerializer):
    title = serializers.CharField(max_length=255)
    content = serializers.CharField(trim_whitespace=False)
    is_private = StrictBooleanField(required=False)
    version = StrictVersionField(min_value=1, required=False)
    attachments = AttachmentWriteSerializer(many=True, required=False, max_length=100)

    def validate_content(self, value):
        try:
            return normalize_note_content(value)[0]
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        items = attrs.get("attachments", [])
        identities = [str(item.get("id") or item.get("url")) for item in items]
        if len(set(identities)) != len(identities):
            raise serializers.ValidationError({"attachments": "Duplicate attachments"})
        return attrs


class NoteAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = ["id", "file_id", "file_name", "file_type", "created_at", "url"]


class NoteReadSerializer(serializers.ModelSerializer):
    attachments = NoteAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = Entry
        fields = ["id", "entity_id", "title", "content", "is_private", "version", "created_at", "updated_at", "attachments"]
        read_only_fields = fields
