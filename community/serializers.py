from rest_framework import serializers
from .models import Realm, Member, RealmFollow, Invite
from user.serializers import AccountPreviewSerializer


class RealmMemberSerializer(serializers.ModelSerializer):
    # The member is the actor_entity; resolve its linked account for display
    # (kept under the `account` key for frontend back-compat).
    account = AccountPreviewSerializer(source="actor_entity.account", read_only=True)
    added_by = AccountPreviewSerializer()

    class Meta:
        model = Member
        fields = "__all__"


class RealmFollowSerializer(serializers.ModelSerializer):
    follower = AccountPreviewSerializer(
        source="actor_entity.account", read_only=True
    )

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
        # Back-compat: the invited user's account id (via the target entity).
        return obj.target_entity.account_id if obj.target_entity else None

    def get_accepted_by_user_id(self, obj):
        return obj.accepted_by_entity.account_id if obj.accepted_by_entity else None

    def get_created_by_id(self, obj):
        return obj.created_by.id if obj.created_by else None

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
            "target_entity",
            "target_user_id",
            "accepted_by_entity",
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
