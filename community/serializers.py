from rest_framework import serializers
from .models import Realm, Member, Follow, Invite
from user.serializers import AccountPreviewSerializer
from entity.serializers import EntitySerializer
from entity.roles import role_tier


class RealmMemberSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    role_name = serializers.CharField(source="role.name", read_only=True)
    role_code = serializers.CharField(source="role.code", read_only=True)
    role_id = serializers.IntegerField(read_only=True)
    entity = EntitySerializer(read_only=True)
    added_by = EntitySerializer(read_only=True)

    def get_role(self, obj):
        return role_tier(obj.role)

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
    role = serializers.SerializerMethodField()
    role_name = serializers.CharField(source="role.name", read_only=True, allow_null=True)
    role_code = serializers.CharField(source="role.code", read_only=True, allow_null=True)
    role_id = serializers.IntegerField(read_only=True, allow_null=True)
    realm_id = serializers.CharField(source="realm.realm_id", read_only=True)
    realm_name = serializers.CharField(source="realm.name", read_only=True)
    realm_type = serializers.CharField(source="realm.type", read_only=True)
    # What the invite page draws: the realm's picture and where it lives, and
    # who sent it - "Maya invited you to ...".
    realm_slug = serializers.CharField(source="realm.slug", read_only=True)
    realm_profile = serializers.CharField(source="realm.profile", read_only=True)
    inviter = EntitySerializer(source="created_by", read_only=True)
    target_entity = EntitySerializer(read_only=True)
    accepted_by_entity = EntitySerializer(read_only=True)
    created_by_id = serializers.SerializerMethodField()

    def get_role(self, obj):
        return role_tier(obj.role) if obj.role_id else None

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
            "realm_slug",
            "realm_profile",
            "kind",
            "status",
            "purpose",
            "role",
            "role_id",
            "role_name",
            "role_code",
            "inviter",
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
    request_pending = serializers.BooleanField(read_only=True, default=False)
    followers_count = serializers.IntegerField(read_only=True)
    members = serializers.IntegerField(read_only=True)
    is_member = serializers.BooleanField(read_only=True)
    is_admin = serializers.BooleanField(read_only=True)
    is_follower = serializers.BooleanField(read_only=True)
    # The acting entity's own role in this realm: "owner"/"admin"/"moderator"/
    # "member", or null when not a member. is_admin cannot answer this - it is
    # true for owner AND admin alike, while several rules (removing or
    # re-roling a fellow admin, transferring ownership, leaving) turn on
    # exactly that distinction.
    #
    # A method field rather than a declared CharField so the serializer still
    # works on querysets that did not apply my_role_annotation: those get null
    # instead of an AttributeError.
    my_role = serializers.SerializerMethodField()
    my_role_id = serializers.IntegerField(read_only=True, allow_null=True, default=None)
    parent = BasicRealmSerializer(allow_null=True)

    def get_my_role(self, obj):
        return getattr(obj, "my_role", None)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        from community.role_policy import feature_availability, realm_capabilities
        from entity.models import Role

        role = Role.objects.filter(pk=data.get("my_role_id")).values("name", "code").first()
        data.update(my_role_name=role["name"] if role else None,
                    my_role_code=role["code"] if role else None,
                    features=feature_availability())
        request = self.context.get("request")
        if request is not None:
            data["capabilities"] = realm_capabilities(getattr(request, "entity", None), instance)
        return data

    class Meta:
        model = Realm
        fields = "__all__"
