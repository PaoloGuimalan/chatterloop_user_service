from rest_framework import serializers
from .models import Realm, Member, Follow, Invite
from user.serializers import AccountPreviewSerializer
from entity.serializers import EntitySerializer


class RealmMemberSerializer(serializers.ModelSerializer):
    entity = EntitySerializer(read_only=True)
    added_by = EntitySerializer(read_only=True)

    class Meta:
        model = Member
        fields = "__all__"


class FollowSerializer(serializers.ModelSerializer):
    follower = EntitySerializer(read_only=True)
    # Expanded so a followers list can render the target without a second
    # lookup - and so a follow of a USER is displayable at all.
    followee = EntitySerializer(read_only=True)
    # Back-compat: `realm` used to be the Realm pk on this payload. Kept so
    # existing clients (webapp + mobile) keep resolving it; null when the
    # followee is a person rather than a page.
    realm = serializers.SerializerMethodField()

    class Meta:
        model = Follow
        fields = "__all__"

    def get_realm(self, obj):
        realm = getattr(obj.followee, "realms", None)
        return str(realm.id) if realm else None


class InviteSerializer(serializers.ModelSerializer):
    realm_id = serializers.CharField(source="realm.realm_id", read_only=True)
    realm_name = serializers.CharField(source="realm.name", read_only=True)
    realm_type = serializers.CharField(source="realm.type", read_only=True)
    target_entity = EntitySerializer(read_only=True)
    accepted_by_entity = EntitySerializer(read_only=True)
    created_by_id = serializers.SerializerMethodField()

    # def get_target_user_id(self, obj):
    #     return obj.target_user.id if obj.target_user else None

    # def get_accepted_by_user_id(self, obj):
    #     return obj.accepted_by_user.id if obj.accepted_by_user else None

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
            "accepted_by_entity",
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
