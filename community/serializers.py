from rest_framework import serializers
from .models import Realm, Member, RealmFollow, Invite
from user.models import Account


def serialize_user_entity_preview(entity):
    if not entity:
        return None
    if entity.entity_type != "user" or entity.source_type != "user.account":
        return {
            "id": entity.id,
            "username": entity.entity_id,
            "first_name": "",
            "middle_name": "",
            "last_name": "",
            "profile": "none",
            "gender": None,
            "is_badged": False,
        }

    account = Account.objects.filter(id=entity.source_id).first()
    if not account:
        return {
            "id": entity.source_id,
            "username": "",
            "first_name": "",
            "middle_name": "",
            "last_name": "",
            "profile": "none",
            "gender": None,
            "is_badged": False,
        }

    return {
        "id": account.id,
        "username": account.username,
        "first_name": account.first_name,
        "middle_name": account.middle_name,
        "last_name": account.last_name,
        "profile": account.profile,
        "gender": account.gender,
        "is_badged": account.is_badged,
    }


class RealmMemberSerializer(serializers.ModelSerializer):
    account = serializers.SerializerMethodField()
    added_by = serializers.SerializerMethodField()

    def get_account(self, obj):
        return serialize_user_entity_preview(obj.account)

    def get_added_by(self, obj):
        return serialize_user_entity_preview(obj.added_by)

    class Meta:
        model = Member
        fields = "__all__"


class RealmFollowSerializer(serializers.ModelSerializer):
    follower = serializers.SerializerMethodField()

    def get_follower(self, obj):
        return serialize_user_entity_preview(obj.follower)

    class Meta:
        model = RealmFollow
        fields = "__all__"


class InviteSerializer(serializers.ModelSerializer):
    realm_id = serializers.CharField(source="realm.realm_id", read_only=True)
    realm_name = serializers.CharField(source="realm.name", read_only=True)
    realm_type = serializers.CharField(source="realm.type", read_only=True)
    target_user_id = serializers.SerializerMethodField()
    accepted_by_user_id = serializers.SerializerMethodField()
    created_by_id = serializers.SerializerMethodField()

    def get_target_user_id(self, obj):
        return obj.target_user.source_id if obj.target_user else None

    def get_accepted_by_user_id(self, obj):
        return obj.accepted_by_user.source_id if obj.accepted_by_user else None

    def get_created_by_id(self, obj):
        return obj.created_by.source_id if obj.created_by else None

    class Meta:
        model = Invite
        fields = [
            "id",
            "realm",
            "realm_id",
            "realm_name",
            "realm_type",
            "kind",
            "status",
            "target_email",
            "target_user",
            "target_user_id",
            "accepted_by_user",
            "accepted_by_user_id",
            "invite_token",
            "created_by",
            "created_by_id",
            "created_at",
            "resolved_at",
        ]


class BasicRealmSerializer(serializers.ModelSerializer):

    class Meta:
        model = Realm
        fields = "__all__"


class RealmSerializer(serializers.ModelSerializer):
    followers_count = serializers.IntegerField(read_only=True)
    members = serializers.IntegerField(read_only=True)
    is_member = serializers.BooleanField(read_only=True)
    is_admin = serializers.BooleanField(read_only=True)
    is_follower = serializers.BooleanField(read_only=True)
    parent = BasicRealmSerializer(allow_null=True)

    class Meta:
        model = Realm
        fields = "__all__"
