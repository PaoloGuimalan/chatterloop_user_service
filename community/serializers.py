from rest_framework import serializers
from .models import Realm, Member, RealmFollow
from user.serializers import AccountPreviewSerializer


class RealmMemberSerializer(serializers.ModelSerializer):
    account = AccountPreviewSerializer()
    added_by = AccountPreviewSerializer()

    class Meta:
        model = Member
        fields = "__all__"


class RealmFollowSerializer(serializers.ModelSerializer):
    follower = AccountPreviewSerializer()

    class Meta:
        model = RealmFollow
        fields = "__all__"


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
