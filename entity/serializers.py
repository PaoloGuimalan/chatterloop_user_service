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
    """
    Realm payload embedded under EntitySerializer.details.

    Carries back-compat display aliases on top of the realm's own fields.
    Every surface that embeds an entity (contacts, members, followers,
    invites, newsfeed) was user-only for a long time, so both clients read a
    counterpart's display identity off username/first_name/middle_name/
    last_name/is_badged. Mapping the realm's fields onto those keys is what
    lets a page appear anywhere an entity is embedded with NO client change.

    The realm's native keys (name, slug, realm_id, type) are all still
    emitted, so consumers that already read those are unaffected - this is
    purely additive.
    """

    # slug is nullable; realm_id is the stable fallback the clients already
    # use for realm routing when a slug is absent.
    username = serializers.SerializerMethodField()
    first_name = serializers.CharField(source="name", read_only=True)
    middle_name = serializers.SerializerMethodField()
    last_name = serializers.SerializerMethodField()
    profile = serializers.SerializerMethodField()
    is_badged = serializers.BooleanField(source="is_verified", read_only=True)

    class Meta:
        model = Realm
        fields = [
            "id",
            "realm_id",
            "name",
            "type",
            "profile",
            "is_active",
            "is_verified",
            "slug",
            # Back-compat display aliases (see docstring).
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "is_badged",
        ]

    def get_username(self, obj):
        return obj.slug or obj.realm_id

    def get_middle_name(self, obj):
        # "N/A" is the sentinel clients already skip when composing a full
        # name, so a realm renders as just its name rather than "Name  ".
        return "N/A"

    def get_last_name(self, obj):
        return ""

    def get_profile(self, obj):
        # Realms use "N/A" for "no picture" while accounts use "none", and
        # clients test entity details against "none". Normalising here means
        # a realm with no picture falls back to initials instead of the
        # avatar trying to load "N/A" as an image URL.
        if not obj.profile or obj.profile in ("N/A", "none"):
            return "none"
        return obj.profile


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
