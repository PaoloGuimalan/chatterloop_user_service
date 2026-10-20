from rest_framework import serializers
from .models import Entry, Attachment, MapView, Mood
from interests.models import Interest
from user_service.services.rabbitmq import RabbitMQClient, Queues
from interests.services.interest_resolver import ensure_grant_override
from newsfeed.services.link_preview import extract_first_url_from_html, get_preview
from diary.encryption import validate_entry_encryption
from django.db import transaction
from user.models import Account


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
        if obj.is_encrypted:
            return None
        url = extract_first_url_from_html(obj.content or "")
        return get_preview(url) if url else None

    class Meta:
        model = Entry
        fields = [
            "id",
            "account",
            "title",
            "content",
            "is_encrypted",
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

    def validate(self, attrs):
        instance = self.instance
        if instance is not None and "account" in attrs and attrs["account"].pk != instance.account_id:
            raise serializers.ValidationError({"account": "An entry's owner cannot be changed."})
        account = instance.account if instance is not None else attrs.get("account")
        if account is not None:
            validate_entry_encryption(
                account, attrs.get("title", instance.title if instance else ""),
                attrs.get("content", instance.content if instance else ""),
                attrs.get("is_encrypted", instance.is_encrypted if instance else False),
                attrs.get("is_private", instance.is_private if instance else True), instance,
            )
        return attrs

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
            RabbitMQClient.publish_on_commit(
                Queues.BUMP_INTEREST_AFFINITY,
                {
                    "entity_id": entry.account.entity_id,
                    "interest_ids": tag_ids,
                    "action": "DIARY_TAG",
                    "is_decrease": False,
                },
            )
            ensure_grant_override(entry.account.entity_id, tag_ids)

    @transaction.atomic
    def create(self, validated_data):
        Account.objects.select_for_update().get(pk=validated_data["account"].pk)
        self.validate(validated_data)
        tag_names = validated_data.pop("tags", [])
        entry = super().create(validated_data)
        self._handle_tags(entry, tag_names)
        return entry

    @transaction.atomic
    def update(self, instance, validated_data):
        Account.objects.select_for_update().get(pk=instance.account_id)
        instance = Entry.objects.select_for_update().get(pk=instance.pk, account_id=instance.account_id)
        self.instance = instance
        self.validate(validated_data)
        tag_names = validated_data.pop("tags", None)
        entry = super().update(instance, validated_data)
        if tag_names is not None:
            self._handle_tags(entry, tag_names)
        return entry
