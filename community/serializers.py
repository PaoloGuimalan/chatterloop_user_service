from rest_framework import serializers
from .models import Realm, Member, RealmFollow


class RealmMemberSerializer(serializers.ModelSerializer):

    class Meta:
        model = Member
        fields = "__all__"


class RealmFollowSerializer(serializers.ModelSerializer):

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
