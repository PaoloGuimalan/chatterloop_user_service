from rest_framework import serializers
from .models import Account, Connection
from entity.serializers import EntitySerializer
from .utils.consent import get_pending_consents


class AccountSerializer(serializers.ModelSerializer):
    is_complete = serializers.BooleanField(read_only=True)
    pending_consents = serializers.ListField(read_only=True)
    # entity = EntitySerializer(read_only=True)

    class Meta:
        model = Account
        fields = [
            "id",
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "birthdate",
            "profile",
            "coverphoto",
            "gender",
            "email",
            "date_created",
            "is_active",
            "is_verified",
            "is_badged",
            "is_private",
            "is_complete",
            "pending_consents",
            # "entity",
        ]
        read_only_fields = ["id", "date_created", "is_complete", "pending_consents"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        account = Account(**validated_data)
        # Hash using Django's default password system or bcrypt before save
        account.set_password(password)
        account.save()
        return account

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance

    def to_representation(self, instance):
        representation = super().to_representation(instance)

        pending_consents = get_pending_consents(instance.entity)
        representation["pending_consents"] = pending_consents
        representation["is_complete"] = (
            instance.is_profile_complete() and not pending_consents
        )
        return representation


class AccountPreviewSerializer(serializers.ModelSerializer):
    entity = EntitySerializer(read_only=True)

    class Meta:
        model = Account
        fields = [
            "id",
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "profile",
            "gender",
            "is_badged",
            "is_private",
            "entity",
        ]


class ConnectionSerializer(serializers.ModelSerializer):
    action_by = EntitySerializer(read_only=True)
    involved_entity = EntitySerializer(read_only=True)

    class Meta:
        model = Connection
        fields = "__all__"


class AccountSearchSerializer(serializers.ModelSerializer):
    has_connection = serializers.BooleanField()
    connection_accomplished = serializers.BooleanField()
    connection_id = serializers.CharField()
    is_action_by_entity = serializers.BooleanField()
    # Raw FK column (account.entity_id) - read straight off the attribute so we
    # don't trigger a join to Entity. Post tagging matches on entity_id, so the
    # client needs it to tag searched users (see NewPostModal tagging flow).
    entity_id = serializers.CharField(read_only=True)

    class Meta:
        model = Account
        fields = [
            "id",
            "entity_id",
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "birthdate",
            "profile",
            "gender",
            "email",
            "date_created",
            "is_active",
            "is_verified",
            "is_private",
            "has_connection",
            "connection_accomplished",
            "connection_id",
            "is_action_by_entity",
        ]
        read_only_fields = ["id", "date_created"]
