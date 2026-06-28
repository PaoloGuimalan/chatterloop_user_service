from rest_framework import serializers
from .models import Account, Connection
from .utils.consent import get_pending_consents
from .utils.entity import resolve_account_from_entity


class AccountSerializer(serializers.ModelSerializer):
    is_complete = serializers.BooleanField(read_only=True)
    pending_consents = serializers.ListField(read_only=True)

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
            "is_complete",
            "pending_consents",
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

        pending_consents = get_pending_consents(instance)
        representation["pending_consents"] = pending_consents
        representation["is_complete"] = (
            instance.is_profile_complete() and not pending_consents
        )
        return representation


class AccountPreviewSerializer(serializers.ModelSerializer):
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
        ]


class ConnectionSerializer(serializers.ModelSerializer):
    action_by = serializers.SerializerMethodField()
    involved_user = serializers.SerializerMethodField()

    def _serialize_entity_account(self, entity):
        account = resolve_account_from_entity(entity)
        if not account:
            return None
        return AccountPreviewSerializer(account).data

    def get_action_by(self, obj):
        return self._serialize_entity_account(obj.action_by)

    def get_involved_user(self, obj):
        return self._serialize_entity_account(obj.involved_user)

    class Meta:
        model = Connection
        fields = "__all__"


class AccountSearchSerializer(serializers.ModelSerializer):
    has_connection = serializers.BooleanField()
    connection_accomplished = serializers.BooleanField()
    connection_id = serializers.CharField()
    is_action_by_user = serializers.BooleanField()

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
            "gender",
            "email",
            "date_created",
            "is_active",
            "is_verified",
            "has_connection",
            "connection_accomplished",
            "connection_id",
            "is_action_by_user",
        ]
        read_only_fields = ["id", "date_created"]
