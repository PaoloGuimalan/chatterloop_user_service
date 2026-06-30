from rest_framework import serializers
from .models import Entity
from user.models import Account
from community.models import Realm


class EmbeddedAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        # Select the specific fields you want to expose to keep payloads clean
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "profile",
            "is_active",
            "gender",
            "is_badged",
            "middle_name",
        ]


class EmbeddedRealmSerializer(serializers.ModelSerializer):
    class Meta:
        model = Realm
        fields = ["id", "realm_id", "name", "type", "profile", "is_active"]


class EntitySerializer(serializers.ModelSerializer):
    # SerializerMethodField allows us to inject dynamic schema data
    details = serializers.SerializerMethodField()

    class Meta:
        model = Entity
        fields = ["id", "type", "details"]

    def get_details(self, obj):
        if obj.type == "user":
            # Check if the relation exists to prevent ObjectDoesNotExist crashes
            if hasattr(obj, "users"):
                return EmbeddedAccountSerializer(obj.users, context=self.context).data

        elif obj.type == "realm":
            if hasattr(obj, "realms"):
                return EmbeddedRealmSerializer(obj.realms, context=self.context).data

        return None
