from rest_framework import serializers
from .models import Account, Connection


class AccountSerializer(serializers.ModelSerializer):
    is_complete = serializers.BooleanField(read_only=True)

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
        ]
        read_only_fields = ["id", "date_created", "is_complete"]

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

    def _get_profile_requirements(self):
        """List of required fields for profile completion - easy to extend"""
        return ["birthdate", "gender"]

    def to_representation(self, instance):
        representation = super().to_representation(instance)

        # Check if all required fields are filled
        required_fields = self._get_profile_requirements()
        is_complete = all(getattr(instance, field, None) for field in required_fields)

        representation["is_complete"] = is_complete
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
    action_by = AccountPreviewSerializer(read_only=True)
    involved_user = AccountPreviewSerializer(read_only=True)

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
